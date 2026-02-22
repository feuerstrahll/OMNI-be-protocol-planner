from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict

import requests

from backend.schemas import PKExtractionResponse


class LLMDisabled(RuntimeError):
    pass


class LLMPKExtractor:
    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 20,
        max_retries: int = 2,
    ) -> None:
        self.provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip()
        self.api_key = (api_key or os.getenv("LLM_API_KEY") or "").strip()
        self.model = (model or os.getenv("LLM_MODEL") or "").strip()
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.logger = logging.getLogger(__name__) 

        if not self.provider or not self.api_key:
            raise LLMDisabled("LLM provider/API key not configured.")

        if self.provider == "yandex":
            if not self.base_url:
                self.base_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
            if not self.model:
                # YandexGPT Pro 5.1 (RC channel)
                self.model = "yandexgpt/rc"
            self.folder_id = (os.getenv("LLM_FOLDER_ID") or os.getenv("YANDEX_FOLDER_ID") or "").strip()
            if not self.folder_id:
                raise LLMDisabled("YANDEX folder id not configured.")
        elif self.provider == "openai_compatible":
            if not self.base_url or not self.model:
                raise LLMDisabled("LLM_BASE_URL and LLM_MODEL must be set for openai_compatible provider.")
        else:
            raise LLMDisabled(f"Unsupported LLM provider: {self.provider}")

    def extract(self, inn: str, pmid: str, abstract_text: str) -> Dict[str, Any]:
        if not abstract_text:
            return {}
        text = abstract_text[:6000]
        messages = self._build_messages(inn, pmid, text)

        try:
            if self.provider == "yandex":
                response_text = self._call_yandex(messages)
            else:
                response_text = self._call_openai_compatible(messages)
        except Exception as exc:
            self.logger.warning("llm_pk_call_failed", exc_info=exc)
            return {}

        payload = self._extract_json(response_text) or {}
        if not payload.get("inn"):
            payload["inn"] = inn or "unknown"
        payload.setdefault("pk_values", [])
        payload.setdefault("ci_values", [])
        payload.setdefault("warnings", [])
        payload.setdefault("missing", [])
        payload.setdefault("validation_issues", [])

        payload = self._sanitize_payload(payload)

        try:
            validated = PKExtractionResponse(**payload)
        except Exception as exc:
            self.logger.warning("llm_pk_validation_failed", exc_info=exc)
            return {}

        return validated.model_dump()

    def _build_messages(self, inn: str, pmid: str, text: str) -> list[dict]:
        system_text = (
            "You are a pharmacokinetics data extraction assistant. "
            "Extract numeric PK values from the provided text. "
            "Return ONLY valid JSON, no explanation."
        )
        user_text = (
            f"Drug: {inn or 'unknown'}\n"
            f"Source: {pmid or 'unknown'}\n\n"
            f"Text:\n{text}\n\n"
            "Return ONLY valid JSON (no markdown) with this schema (use null when missing):\n"
            "{\n"
            '  "pk_values": [\n'
            '    {"name": "Cmax", "value": 245.0, "unit": "ng/mL", '
            '"evidence": [{"excerpt": "...", "pmid_or_url": "PMID:123", "location": "abstract"}]},\n'
            '    {"name": "AUC0-t", "value": 1850.0, "unit": "ng*h/mL", '
            '"evidence": [{"excerpt": "...", "pmid_or_url": "PMID:123", "location": "abstract"}]},\n'
            '    {"name": "CVintra", "value": 34.0, "unit": "%", '
            '"evidence": [{"excerpt": "...", "pmid_or_url": "PMID:123", "location": "abstract"}]}\n'
            "  ],\n"
            '  "ci_values": [\n'
            '    {"param": "Cmax", "ci_low": 0.90, "ci_high": 1.10, "ci_type": "ratio", '
            '"confidence_level": 0.90, "n": 24, "gmr": 1.00, "design_hint": "2x2_crossover", '
            '"evidence": [{"excerpt": "...", "pmid_or_url": "PMID:123", "location": "abstract"}]}\n'
            "  ],\n"
            '  "study_condition": "fed" | "fasted" | "unknown",\n'
            '  "meal_details": {"calories_kcal": 800, "fat_g": 50, "timing_min": 30, "note": "high-fat meal"},\n'
            '  "design_hints": {"is_crossover_2x2": true, "log_transform": true, "n": 24},\n'
            '  "warnings": [],\n'
            '  "missing": [],\n'
            '  "validation_issues": []\n'
            "}\n\n"
            "Rules:\n"
            "- Output ONLY this single JSON object. No markdown. Do not repeat keys; each array element is one object.\n"
            "- Maximum 20 items in pk_values and 20 in ci_values. Do not duplicate names/params.\n"
            "- Do NOT invent numbers.\n"
            "- pk_values: return ONLY for these canonical names: Cmax, AUC, AUC0-t, AUC0-inf, Tmax, t1/2, lambda_z, CVintra.\n"
            "- CVintra is intra-subject / within-subject CV ONLY. Synonyms: CVw, CV_w, within-subject CV, intra-subject CV.\n"
            "- If Swr/within-subject SD is given without %CV, do NOT convert; leave CVintra as null.\n"
            "- Ignore unrelated percent values (absolute bioavailability, protein binding, % excreted, etc.).\n"
            "- Evidence excerpt must include the number (<=300 chars) and pmid_or_url must be the given Source (e.g. PMID:... or PMCID:...).\n"
        )
        return [
            {"role": "system", "text": system_text},
            {"role": "user", "text": user_text},
        ]

    def _call_yandex(self, messages: list[dict]) -> str:
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": self.folder_id,
            "Content-Type": "application/json",
        }
        body = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": "2000"},
            "messages": messages,
            "jsonSchema": {"schema": self._pk_json_schema()},
        }
        resp = self._post_with_retries(self.base_url, body, headers)
        payload = resp.json()
        return payload["result"]["alternatives"][0]["message"]["text"]

    def _call_openai_compatible(self, messages: list[dict]) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": messages[0]["text"]},
                {"role": "user", "content": messages[1]["text"]},
            ],
        }
        resp = self._post_with_retries(self.base_url, body, headers)
        payload = resp.json()
        return payload["choices"][0]["message"]["content"]

    def _post_with_retries(self, url: str, body: Dict[str, Any], headers: Dict[str, str]) -> requests.Response:
        last_exc: Exception | None = None
        retry_statuses = {429, 500, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
                if resp.status_code in retry_statuses:
                    if attempt < self.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("LLM request failed without exception.")

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any] | None:
        text_s = (text or "").strip()
        if text_s.startswith(("{", "[")):
            try:
                return json.loads(text_s)
            except Exception:
                pass
        cleaned = text_s.replace("```json", "").replace("```", "").strip()
        obj_str = LLMPKExtractor._extract_balanced_json_object(cleaned)
        if not obj_str:
            return None
        try:
            return json.loads(obj_str)
        except Exception:
            return None

    @staticmethod
    def _extract_balanced_json_object(text: str) -> str | None:
        """Extract first balanced {...} object (ignore braces inside strings)."""
        s = (text or "").strip()
        i = s.find("{")
        if i < 0:
            return None
        start = i
        depth = 1
        i += 1
        in_dq = False
        escape = False
        while i < len(s) and depth > 0:
            c = s[i]
            if in_dq:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_dq = False
                i += 1
                continue
            if c == '"':
                in_dq = True
                i += 1
                continue
            if c == "{":
                depth += 1
                i += 1
                continue
            if c == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
                i += 1
                continue
            i += 1
        return None

    @staticmethod
    def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed_top = {
            "inn",
            "pk_values",
            "ci_values",
            "study_condition",
            "meal_details",
            "design_hints",
            "warnings",
            "missing",
            "validation_issues",
        }
        clean: Dict[str, Any] = {key: payload.get(key) for key in allowed_top if key in payload}
        meal_details = payload.get("meal_details")
        if isinstance(meal_details, dict):
            allowed_meal = {"calories_kcal", "fat_g", "timing_min", "note"}
            clean["meal_details"] = {key: meal_details.get(key) for key in allowed_meal if key in meal_details}
        design_hints = payload.get("design_hints")
        if isinstance(design_hints, dict):
            allowed_hints = {"is_crossover_2x2", "log_transform", "n"}
            clean["design_hints"] = {key: design_hints.get(key) for key in allowed_hints if key in design_hints}

        def sanitize_evidence(ev: Dict[str, Any]) -> Dict[str, Any]:
            allowed_ev = {
                "source_id",
                "pmid_or_url",
                "pmid",
                "url",
                "excerpt",
                "location",
                "confidence",
                "context_tags",
                "offset_start",
                "offset_end",
                "source_type",
                "source",
                "snippet",
                "context",
            }
            return {key: ev.get(key) for key in allowed_ev if key in ev}

        def sanitize_pk(pk: Dict[str, Any]) -> Dict[str, Any]:
            allowed_pk = {
                "name",
                "value",
                "unit",
                "normalized_value",
                "normalized_unit",
                "evidence",
                "warnings",
                "conflict_sources",
                "ambiguous_condition",
            }
            clean_pk = {key: pk.get(key) for key in allowed_pk if key in pk}
            evidence = pk.get("evidence") or []
            clean_pk["evidence"] = [sanitize_evidence(ev) for ev in evidence if isinstance(ev, dict)]
            # Normalize name: map LLM variants to canonical names
            name = (clean_pk.get("name") or "").strip()
            name_l = name.lower().replace("_", "").replace(" ", "").replace("-", "")
            if any(k in name_l for k in ["cvintra", "withinsubjectcv", "intrasubjectcv", "cvw"]):
                clean_pk["name"] = "CVintra"
            elif name_l in ("cmax", "cmax."):
                clean_pk["name"] = "Cmax"
            elif name_l in ("auc0t", "auc0-t"):
                clean_pk["name"] = "AUC0-t"
            elif name_l in ("auc0inf", "auc0-inf", "auc0∞"):
                clean_pk["name"] = "AUC0-inf"
            return clean_pk

        def sanitize_ci(ci: Dict[str, Any]) -> Dict[str, Any]:
            allowed_ci = {
                "param",
                "ci_low",
                "ci_high",
                "ci_type",
                "confidence_level",
                "n",
                "design_hint",
                "gmr",
                "evidence",
                "warnings",
                "ambiguous_condition",
            }
            clean_ci = {key: ci.get(key) for key in allowed_ci if key in ci}
            evidence = ci.get("evidence") or []
            clean_ci["evidence"] = [sanitize_evidence(ev) for ev in evidence if isinstance(ev, dict)]
            return clean_ci

        clean["pk_values"] = [sanitize_pk(pk) for pk in payload.get("pk_values") or [] if isinstance(pk, dict)]
        clean["ci_values"] = [sanitize_ci(ci) for ci in payload.get("ci_values") or [] if isinstance(ci, dict)]
        return clean

    @staticmethod
    def _pk_json_schema() -> Dict[str, Any]:
        """JSON Schema for YandexGPT structured output."""
        pk_item = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": ["number", "null"]},
                "unit": {"type": ["string", "null"]},
                "normalized_value": {"type": ["number", "null"]},
                "normalized_unit": {"type": ["string", "null"]},
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "evidence": {"type": "array", "items": {"type": "object"}},
                "conflict_sources": {"type": ["array", "null"]},
                "ambiguous_condition": {"type": ["boolean", "null"]},
            },
            "required": ["name", "value"],
        }
        ci_item = {
            "type": "object",
            "properties": {
                "param": {"type": "string"},
                "ci_low": {"type": ["number", "null"]},
                "ci_high": {"type": ["number", "null"]},
                "ci_type": {"type": ["string", "null"]},
                "confidence_level": {"type": ["number", "null"]},
                "n": {"type": ["integer", "null"]},
                "design_hint": {"type": ["string", "null"]},
                "gmr": {"type": ["number", "null"]},
                "warnings": {"type": "array", "items": {"type": "string"}, "default": []},
                "evidence": {"type": "array", "items": {"type": "object"}},
                "ambiguous_condition": {"type": ["boolean", "null"]},
            },
            "required": ["param", "ci_low", "ci_high"],
        }
        return {
            "type": "object",
            "properties": {
                "inn": {"type": ["string", "null"]},
                "pk_values": {"type": "array", "items": pk_item, "default": []},
                "ci_values": {"type": "array", "items": ci_item, "default": []},
                "study_condition": {"type": ["string", "null"]},
                "meal_details": {"type": ["object", "null"]},
                "design_hints": {"type": ["object", "null"]},
                "warnings": {"type": "array", "items": {"type": "string"}, "default": []},
                "missing": {"type": "array", "items": {"type": "string"}, "default": []},
                "validation_issues": {"type": "array", "items": {"type": "object"}, "default": []},
            },
            "required": ["pk_values", "ci_values"],
        }

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)


def _is_valid_evidence_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    s = url.strip().lower()
    return s.startswith("pmid:") or s.startswith("pmcid:") or s.startswith("http")


def _filter_evidence_to_valid_urls(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Drop evidence entries where pmid_or_url does not start with PMID:/PMCID:/http."""
    out = dict(parsed)
    for key in ("pk_values", "ci_values"):
        items = out.get(key)
        if not isinstance(items, list):
            continue
        new_list = []
        for item in items:
            if not isinstance(item, dict):
                new_list.append(item)
                continue
            ev_list = item.get("evidence")
            if not ev_list:
                new_list.append(item)
                continue
            filtered_ev = [
                e for e in ev_list
                if isinstance(e, dict) and _is_valid_evidence_url(e.get("pmid_or_url"))
            ]
            new_item = dict(item)
            new_item["evidence"] = filtered_ev
            new_list.append(new_item)
        out[key] = new_list
    return out


class YandexLLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        folder_id: Optional[str] = None,
        model: str = "yandexgpt/rc",
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key or os.getenv("YANDEX_API_KEY")
        self.folder_id = folder_id or os.getenv("YANDEX_FOLDER_ID")
        self.model = model
        self.base_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        self.max_retries = max(0, int(max_retries))

    def extract_pk_from_text(
        self,
        text: str,
        inn: str,
        *,
        source_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.api_key or not self.folder_id:
            return {}
        try:
            truncated = (text or "")[:60000]
            evidence_rules = (
                "Evidence: each item must have pmid_or_url starting with PMID: or PMCID: or https://, "
                "and location from the context label. "
                f"If provided, use source_id={source_id!r} and location={location!r} in evidence.\n"
                if (source_id or location)
                else ""
            )
            payload = {
                "modelUri": f"gpt://{self.folder_id}/{self.model}",
                "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": "2000"},
                "messages": [
                    {
                        "role": "user",
                        "text": (
                            "You are an expert pharmacokinetics data extraction assistant. "
                            "Extract numeric PK values from the provided text and return ONLY valid JSON. "
                            f"Drug: {inn}\n\nText:\n{truncated}\n\n"
                            "Return JSON strictly matching this structure:\n"
                            "{\n"
                            '  "pk_values": [\n'
                            '    {"name": "Cmax", "value": 245.0, "unit": "ng/mL", "evidence": [{"excerpt": "...", "pmid_or_url": "PMCID:123", "location": "sec:snippets"}]},\n'
                            '    {"name": "AUC", "value": 1850.0, "unit": "ng*h/mL"},\n'
                            '    {"name": "CVintra", "value": 34.0, "unit": "%"}\n'
                            "  ],\n"
                            '  "ci_values": []\n'
                            "}\n"
                            "Rules:\n"
                            "- CVintra means intra-subject (within-subject) CV only\n"
                            "- If a value is missing, DO NOT invent it\n"
                            f"- {evidence_rules}"
                            "- Output ONLY valid JSON, without Markdown blocks like ```json"
                        ),
                    }
                ],
                "jsonObject": True,
            }
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
                "Content-Type": "application/json",
            }
            resp = self._post_with_retries(payload, headers)
            if resp.status_code != 200:
                logger.warning(f"yandex_llm_http_error: status={resp.status_code}, text={resp.text[:200]}")
                return {}
            data = resp.json()
            text_out = (
                data.get("result", {})
                .get("alternatives", [{}])[0]
                .get("message", {})
                .get("text", "")
            ).strip()
            parsed: Dict[str, Any] | None = None
            if text_out.startswith(("{", "[")):
                try:
                    parsed = json.loads(text_out)
                except Exception:
                    parsed = None
            if parsed is None:
                cleaned = text_out.replace("```json", "").replace("```", "").strip()
                match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                    except Exception:
                        parsed = None
            if parsed:
                parsed = _filter_evidence_to_valid_urls(parsed)
            return parsed or {}
        except Exception as exc:
            logger.warning(f"yandex_llm_error: {str(exc)}")
            return {}

    def translate_inn_ru_to_en(self, inn_ru: str) -> Dict[str, Any]:
        """Переводит МНН с русского на английский (INN для PubMed). Возвращает {"inn_en": str, "synonyms": list}."""
        if not self.api_key or not self.folder_id:
            return {"inn_en": "", "synonyms": []}
        inn_ru = (inn_ru or "").strip()
        if not inn_ru:
            return {"inn_en": "", "synonyms": []}
        try:
            payload = {
                "modelUri": f"gpt://{self.folder_id}/{self.model}",
                "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": "300"},
                "messages": [
                    {
                        "role": "user",
                        "text": (
                            "You are a pharmaceutical/INN (International Nonproprietary Name) expert. "
                            "Given a drug name in Russian (Cyrillic), return its official English INN only. "
                            "Reply with valid JSON only, no markdown. Format:\n"
                            '{"inn_en": "english_inn", "synonyms": ["optional variant 1", "optional variant 2"]}\n'
                            f"Russian drug name: {inn_ru}\n"
                            "Rules: inn_en must be Latin script, lowercase; synonyms are optional alternative spellings or trade names."
                        ),
                    }
                ],
                "jsonObject": True,
            }
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
                "Content-Type": "application/json",
            }
            resp = self._post_with_retries(payload, headers)
            if resp.status_code != 200:
                logger.warning(f"yandex_llm_http_error: status={resp.status_code}, text={resp.text[:200]}")
                return {"inn_en": "", "synonyms": []}
            data = resp.json()
            text_out = (
                data.get("result", {})
                .get("alternatives", [{}])[0]
                .get("message", {})
                .get("text", "")
            ).strip()
            parsed: Dict[str, Any] | None = None
            if text_out.startswith(("{", "[")):
                try:
                    parsed = json.loads(text_out)
                except Exception:
                    parsed = None
            if parsed is None:
                cleaned = text_out.replace("```json", "").replace("```", "").strip()
                match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                    except Exception:
                        parsed = None
            if parsed is None:
                return {"inn_en": "", "synonyms": []}
            out = parsed
            inn_en = (out.get("inn_en") or "").strip().lower()
            syns = out.get("synonyms") or []
            if isinstance(syns, list):
                syns = [str(s).strip() for s in syns if s]
            return {"inn_en": inn_en, "synonyms": syns}
        except Exception as exc:
            logger.warning(f"yandex_llm_translate_error: {str(exc)}")
            return {"inn_en": "", "synonyms": []}

    def _post_with_retries(self, payload: Dict[str, Any], headers: Dict[str, str]) -> requests.Response:
        retry_statuses = {429, 500, 502, 503, 504}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(self.base_url, headers=headers, json=payload, timeout=15)
                if resp.status_code in retry_statuses:
                    if attempt < self.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Yandex LLM request failed without exception.")

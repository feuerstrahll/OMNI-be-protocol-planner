from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from backend.schemas import CIValue, Evidence, PKValue
from backend.services.utils import normalize_space, safe_float


class PKExtractor:
    def __init__(self, llm_client=None, pmc_fetcher=None, llm_extractor=None) -> None:
        self.llm_client = llm_client
        self.pmc_fetcher = pmc_fetcher
        self.llm_extractor = llm_extractor
        self.last_context: Dict[str, Any] = {}
        self.last_warnings: List[str] = []
        self.patterns = {
            "Cmax": re.compile(
                r"(C\s*max|C_max)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*(ng/mL|mg/L|µg/L|ug/L|ng/L|mg/mL|µg/mL|ug/mL)",
                re.IGNORECASE,
            ),
            "AUC": re.compile(
                r"(AUC(?:0-?t|0-?inf|0-?∞)?)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*([a-zA-Zµμ/\*\-\.]+)",
                re.IGNORECASE,
            ),
            "t1/2": re.compile(
                r"(t\s*1\s*/\s*2|half\s*-?life)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*(h|hr|hours)",
                re.IGNORECASE,
            ),
            "Tmax": re.compile(
                r"(T\s*max|T_max)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*(h|hr|hours|min)",
                re.IGNORECASE,
            ),
            "CVintra": re.compile(
                r"(intra[^\d]{0,20}CV|CV[^\d]{0,20}intra|CV[^\d]{0,20}within)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*%",
                re.IGNORECASE,
            ),
            "lambda_z": re.compile(
                r"(lambda[_\s-]*z|z[_\s-]*lambda|elimination\s*rate\s*constant)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)\s*(1/h|h\^-1|hr\^-1)",
                re.IGNORECASE,
            ),
        }
        self.ci_pattern = re.compile(
            r"(?P<cl>90|95)\s*%?\s*CI[^\d]{0,10}\(?\s*(?P<low>\d+(?:\.\d+)?)\s*(?:-|–|to|,|;)\s*(?P<high>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        self.gmr_pattern = re.compile(
            r"(GMR|geometric\s*mean\s*ratio|T\s*/\s*R|test\s*/\s*reference|test-to-reference)\s*(?:=|:)?\s*(\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        self.n_pattern = re.compile(r"\b(n|N)\s*=?\s*(\d{2,4})\b")

    def extract(self, abstracts: Dict[str, str], inn: str | None = None) -> Tuple[List[PKValue], List[CIValue], List[str]]:
        """Extract PK/CI values collecting all matches, annotating conflicts with sources, and expanding/context-tagging snippets.

        Also supports optional hybrid LLM augmentation (when configured) and derives study context
        (fed/fasted, meal details, design hints) without changing the public return signature.
        """
        pk_values: List[PKValue] = []
        ci_values: List[CIValue] = []
        found_metrics = set()

        self.last_context = {"study_condition": "unknown", "meal_details": None, "design_hints": None}
        self.last_warnings = []
        study_flags = {"fed": False, "fasted": False}
        meal_details: Dict[str, Any] | None = None
        design_hints: Dict[str, Any] = {"is_crossover_2x2": None, "log_transform": None, "n": None}
        ambiguous_sources: set[str] = set()

        for source_id, text in abstracts.items():
            clean_text = normalize_space(text)
            if not clean_text:
                continue
            condition, meal_candidate, fed_detected, fasted_detected, conflict = self._infer_study_condition(clean_text)
            if fed_detected:
                study_flags["fed"] = True
            if fasted_detected:
                study_flags["fasted"] = True
            if condition == "fed":
                meal_details = self._merge_meal_details(meal_details, meal_candidate)
            if conflict:
                ambiguous_sources.add(source_id)

            design_hints = self._merge_design_hints(design_hints, self._infer_design_hints(clean_text))
            design_hint = self._infer_design_hint(clean_text)
            per_source_metrics: Dict[str, List[Tuple[float, int]]] = {}
            # Regex-based extraction from abstracts (MVP).
            for metric, pattern in self.patterns.items():
                for match in pattern.finditer(clean_text):
                    value = safe_float(match.group(2))
                    unit = match.group(3) if match.lastindex and match.lastindex >= 3 else "%"
                    if value is None:
                        continue
                    metric_name = metric
                    if metric == "AUC":
                        label = (match.group(1) or "").lower()
                        if "inf" in label or "∞" in label:
                            metric_name = "AUC0-inf"
                        elif "0-t" in label or "0t" in label:
                            metric_name = "AUC0-t"
                        else:
                            metric_name = "AUC"

                    metric_entries = per_source_metrics.setdefault(metric_name, [])
                    if self._value_exists(metric_entries, value):
                        continue

                    snippet = self._make_snippet(clean_text, match.start(), match.end())
                    context_tags = self._context_tags(snippet)
                    evidence = self._build_evidence(
                        source_id,
                        snippet,
                        context_tags,
                        offset_start=match.start(),
                        offset_end=match.end(),
                    )

                    warnings: List[str] = []
                    ambiguous = source_id in ambiguous_sources
                    if ambiguous:
                        warnings.append("ambiguous_condition")
                    if context_tags.get("animal") and not context_tags.get("human"):
                        warnings.append("animal_study_warning")
                    if not context_tags.get("fasted") and not context_tags.get("fed"):
                        warnings.append("feeding_condition_unknown")

                    conflict_sources, conflict_warnings = self._detect_conflicts(
                        pk_values, metric_name, value, source_id
                    )
                    warnings.extend(conflict_warnings)

                    pk_values.append(
                        PKValue(
                            name=metric_name,
                            value=value,
                            unit=unit,
                            evidence=[evidence],
                            warnings=warnings,
                            conflict_sources=conflict_sources or None,
                            ambiguous_condition=ambiguous or None,
                        )
                    )
                    metric_entries.append((value, len(pk_values) - 1))

                    if len(metric_entries) > 1:
                        for _, idx in metric_entries:
                            self._add_warning(pk_values[idx], "multiple_values_in_source")

                    found_metrics.add(metric_name)

            for match in self.ci_pattern.finditer(clean_text):
                ci_low = safe_float(match.group("low"))
                ci_high = safe_float(match.group("high"))
                if ci_low is None or ci_high is None:
                    continue
                snippet = self._make_snippet(clean_text, match.start(), match.end())
                param = self._infer_ci_param(snippet)
                if not param:
                    continue
                cl = match.group("cl")
                confidence_level = float(cl) / 100.0 if cl else 0.90
                gmr = self._infer_gmr(snippet)
                n_val = self._infer_n(snippet)
                warnings: List[str] = []
                ambiguous = source_id in ambiguous_sources
                if ambiguous:
                    warnings.append("ambiguous_condition")
                if confidence_level != 0.90:
                    warnings.append("confidence_level_not_90")
                ci_type = "ratio"
                low_str = f"{ci_low}"
                high_str = f"{ci_high}"
                if re.search(rf"{re.escape(low_str)}\s*%", snippet) or re.search(
                    rf"{re.escape(high_str)}\s*%", snippet
                ):
                    ci_type = "percent"
                context_tags = self._context_tags(snippet)
                evidence = self._build_evidence(
                    source_id,
                    snippet,
                    context_tags,
                    offset_start=match.start(),
                    offset_end=match.end(),
                )
                ci_values.append(
                    CIValue(
                        param=param,
                        ci_low=ci_low,
                        ci_high=ci_high,
                        ci_type=ci_type,
                        confidence_level=confidence_level,
                        gmr=gmr,
                        n=n_val,
                        design_hint=design_hint,
                        evidence=[evidence],
                        warnings=warnings,
                        ambiguous_condition=ambiguous or None,
                    )
                )

        if self.llm_extractor is not None:
            for source_id, text in abstracts.items():
                clean_text = normalize_space(text)
                if not clean_text:
                    continue
                try:
                    llm_data = self.llm_extractor.extract(inn=inn or "", pmid=source_id, abstract_text=clean_text)
                except Exception:
                    continue
                if not llm_data:
                    continue
                try:
                    pk_values, ci_values, found_metrics = self._merge_llm_output(
                        llm_data,
                        source_id,
                        clean_text,
                        pk_values,
                        ci_values,
                        found_metrics,
                        source_id in ambiguous_sources,
                    )
                    study_flags, meal_details, design_hints = self._merge_llm_context(
                        llm_data,
                        study_flags,
                        meal_details,
                        design_hints,
                    )
                except Exception:
                    continue

        if "CVintra" not in found_metrics and self.llm_client is not None:
            for source_id, text in abstracts.items():
                if not source_id.startswith("PMCID:"):
                    continue
                full_text = ""
                if self.pmc_fetcher is not None:
                    try:
                        full_text = self.pmc_fetcher(source_id)
                    except Exception:
                        full_text = ""
                if not full_text:
                    full_text = text
                llm_result = self.llm_client.extract_pk_from_text(full_text, inn="")
                cv_val = llm_result.get("CVintra")
                if cv_val is None:
                    continue
                try:
                    cv_float = float(cv_val)
                except Exception:
                    continue
                pmc_url = self._pmc_url(source_id)
                evidence = Evidence(
                    source_id=source_id,
                    pmid_or_url=pmc_url,
                    pmid=None,
                    url=pmc_url,
                    excerpt="Extracted via YandexGPT from PMC full text",
                    location="full_text_llm",
                )
                llm_warnings = ["llm_extracted_requires_human_review"]
                if source_id in ambiguous_sources:
                    llm_warnings.append("ambiguous_condition")
                pk_values.append(
                    PKValue(
                        name="CVintra",
                        value=cv_float,
                        unit="%",
                        evidence=[evidence],
                        warnings=llm_warnings,
                        ambiguous_condition=(source_id in ambiguous_sources) or None,
                    )
                )
                found_metrics.add("CVintra")

                ci_low = llm_result.get("CI_low")
                ci_high = llm_result.get("CI_high")
                ci_param = llm_result.get("CI_param")
                if ci_low is not None and ci_high is not None and ci_param in ("AUC", "Cmax"):
                    try:
                        ci_low_f = float(ci_low)
                        ci_high_f = float(ci_high)
                    except Exception:
                        ci_low_f = None
                        ci_high_f = None
                    if ci_low_f is not None and ci_high_f is not None:
                        ci_warnings = ["llm_extracted_requires_human_review"]
                        if source_id in ambiguous_sources:
                            ci_warnings.append("ambiguous_condition")
                        ci_values.append(
                            CIValue(
                                param=ci_param,
                                ci_low=ci_low_f,
                                ci_high=ci_high_f,
                                ci_type="ratio",
                                confidence_level=0.90,
                                n=llm_result.get("n"),
                                design_hint=None,
                                gmr=None,
                                evidence=[
                                    Evidence(
                                        source_id=source_id,
                                        pmid_or_url=pmc_url,
                                        pmid=None,
                                        url=pmc_url,
                                        excerpt="Extracted via YandexGPT from PMC full text",
                                        location="full_text_llm",
                                    )
                                ],
                                warnings=ci_warnings,
                                ambiguous_condition=(source_id in ambiguous_sources) or None,
                            )
                        )
                break

        study_condition = self._final_study_condition(study_flags)
        if ambiguous_sources:
            study_condition = "unknown"
            self.last_warnings.append("feeding_condition_conflict")
        if study_condition == "fed" and not self._has_meal_details(meal_details):
            self.last_warnings.append("clarify_meal_composition")

        if design_hints.get("n") is None:
            n_candidates = [ci.n for ci in ci_values if ci.n is not None]
            if n_candidates:
                design_hints["n"] = max(n_candidates)

        self.last_context = {
            "study_condition": study_condition,
            "meal_details": meal_details if self._has_meal_details(meal_details) else None,
            "design_hints": design_hints,
        }

        auc_found = any(m in found_metrics for m in ["AUC", "AUC0-inf", "AUC0-t"])
        missing = []
        for m in ["Cmax", "AUC", "t1/2", "CVintra"]:
            if m == "AUC" and auc_found:
                continue
            if m not in found_metrics:
                missing.append(m)
        return pk_values, ci_values, missing

    @staticmethod
    def _make_snippet(text: str, start: int, end: int, window: int = 200) -> str:
        left = max(0, start - window)
        right = min(len(text), end + window)
        snippet = text[left:right]
        return snippet[:400]

    @staticmethod
    def _build_evidence(
        source_id: str,
        snippet: str,
        context_tags: Dict[str, bool] | None = None,
        offset_start: int | None = None,
        offset_end: int | None = None,
        location: str | None = "abstract",
    ) -> Evidence:
        source_is_pmc = source_id.startswith("PMCID:")
        pmc_id = source_id.replace("PMCID:", "")
        if pmc_id and not pmc_id.upper().startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
        url = None if not source_is_pmc else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/"
        pmid_or_url = url or f"PMID:{source_id}"
        return Evidence(
            source_id=source_id,
            pmid_or_url=pmid_or_url,
            pmid=source_id if not source_is_pmc else None,
            url=url,
            excerpt=snippet,
            location=location,
            context_tags=context_tags,
            offset_start=offset_start,
            offset_end=offset_end,
        )

    @staticmethod
    def _infer_ci_param(snippet: str) -> str | None:
        text = snippet.lower()
        if "cmax" in text:
            return "Cmax"
        if "auc" in text:
            return "AUC"
        return None

    def _infer_gmr(self, snippet: str) -> float | None:
        match = self.gmr_pattern.search(snippet)
        if not match:
            return None
        return safe_float(match.group(2))

    def _infer_n(self, snippet: str) -> int | None:
        match = self.n_pattern.search(snippet)
        if not match:
            return None
        try:
            return int(match.group(2))
        except Exception:
            return None

    @staticmethod
    def _infer_design_hint(text: str) -> str | None:
        hints: List[str] = []
        text_l = text.lower()
        if "2x2" in text_l or "2×2" in text_l or "crossover" in text_l:
            hints.append("2x2_crossover")
        if "log" in text_l and "transform" in text_l:
            hints.append("log_transformed")
        return "; ".join(hints) if hints else None

    @staticmethod
    def _context_tags(snippet: str) -> Dict[str, bool]:
        text = snippet.lower()
        return {
            "fasted": any(term in text for term in ["fasted", "fasting", "overnight fast", "empty stomach"]),
            "fed": any(
                term in text
                for term in ["fed", "high-fat meal", "high fat meal", "standard meal", "after meal", "postprandial"]
            ),
            "human": any(term in text for term in ["subject", "volunteer", "patient", "human"]),
            "animal": any(term in text for term in ["rat", "dog", "rabbit", "animal", "mouse"]),
            "crossover": any(term in text for term in ["crossover", "cross-over", "2x2", "2×2"]),
            "log_transformed": any(term in text for term in ["log-transformed", "ln(", "log-scale"]),
        }

    def _infer_study_condition(
        self, text: str
    ) -> tuple[str, Dict[str, Any] | None, bool, bool, bool]:
        text_l = text.lower()
        fasted_terms = ["fasted", "fasting", "overnight fast", "empty stomach"]
        fed_terms = [
            "fed",
            "high-fat meal",
            "high fat meal",
            "standard meal",
            "after meal",
            "postprandial",
        ]
        fasted = any(term in text_l for term in fasted_terms)
        fed = any(term in text_l for term in fed_terms)
        condition = "unknown"
        conflict = fed and fasted
        if fed and not fasted:
            condition = "fed"
        elif fasted and not fed:
            condition = "fasted"
        meal_details = self._infer_meal_details(text_l) if fed else None
        return condition, meal_details, fed, fasted, conflict

    @staticmethod
    def _infer_meal_details(text_l: str) -> Dict[str, Any] | None:
        details: Dict[str, Any] = {}
        kcal_match = re.search(r"(\d{2,4})\s*kcal", text_l)
        if kcal_match:
            try:
                details["calories_kcal"] = int(kcal_match.group(1))
            except Exception:
                pass
        fat_match = re.search(r"(\d{1,3})\s*g\s*fat", text_l)
        if fat_match:
            try:
                details["fat_g"] = int(fat_match.group(1))
            except Exception:
                pass
        timing_match = re.search(r"(?:after|post)\s*(?:the\s*)?meal[^0-9]{0,10}(\d{1,3})\s*min", text_l)
        if not timing_match:
            timing_match = re.search(r"(\d{1,3})\s*min[^a-z]{0,5}(?:after|post)\s*(?:the\s*)?meal", text_l)
        if timing_match:
            try:
                details["timing_min"] = int(timing_match.group(1))
            except Exception:
                pass
        note = None
        if "high-fat meal" in text_l or "high fat meal" in text_l:
            note = "high-fat meal"
        elif "standard meal" in text_l:
            note = "standard meal"
        if note:
            details["note"] = note
        return details or None

    @staticmethod
    def _merge_meal_details(
        existing: Dict[str, Any] | None, incoming: Dict[str, Any] | None
    ) -> Dict[str, Any] | None:
        if not incoming:
            return existing
        if not existing:
            return dict(incoming)
        merged = dict(existing)
        for key, value in incoming.items():
            if merged.get(key) is None and value is not None:
                merged[key] = value
        return merged

    @staticmethod
    def _has_meal_details(details: Dict[str, Any] | None) -> bool:
        if not details:
            return False
        return any(details.get(key) is not None for key in ["calories_kcal", "fat_g", "timing_min", "note"])

    def _infer_design_hints(self, text: str) -> Dict[str, Any]:
        text_l = text.lower()
        hints: Dict[str, Any] = {
            "is_crossover_2x2": bool(
                "2x2" in text_l or "2×2" in text_l or "crossover" in text_l or "cross-over" in text_l
            ),
            "log_transform": bool(
                ("log" in text_l and "transform" in text_l) or "log-transformed" in text_l or "log-scale" in text_l
            ),
            "n": None,
        }
        n_val = self._infer_n(text)
        if n_val is not None:
            hints["n"] = n_val
        return hints

    @staticmethod
    def _merge_design_hints(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        if not incoming:
            return current
        merged = dict(current)
        if incoming.get("is_crossover_2x2") is True:
            merged["is_crossover_2x2"] = True
        elif merged.get("is_crossover_2x2") is None and incoming.get("is_crossover_2x2") is False:
            merged["is_crossover_2x2"] = False

        if incoming.get("log_transform") is True:
            merged["log_transform"] = True
        elif merged.get("log_transform") is None and incoming.get("log_transform") is False:
            merged["log_transform"] = False

        incoming_n = incoming.get("n")
        if isinstance(incoming_n, int):
            current_n = merged.get("n")
            if current_n is None or incoming_n > current_n:
                merged["n"] = incoming_n
        return merged

    @staticmethod
    def _final_study_condition(study_flags: Dict[str, bool]) -> str:
        if study_flags.get("fed") and not study_flags.get("fasted"):
            return "fed"
        if study_flags.get("fasted") and not study_flags.get("fed"):
            return "fasted"
        return "unknown"

    def _merge_llm_context(
        self,
        llm_data: Dict[str, Any],
        study_flags: Dict[str, bool],
        meal_details: Dict[str, Any] | None,
        design_hints: Dict[str, Any],
    ) -> tuple[Dict[str, bool], Dict[str, Any] | None, Dict[str, Any]]:
        condition = llm_data.get("study_condition")
        if condition == "fed":
            study_flags["fed"] = True
            meal_details = self._merge_meal_details(meal_details, llm_data.get("meal_details"))
        elif condition == "fasted":
            study_flags["fasted"] = True

        hints = llm_data.get("design_hints")
        if isinstance(hints, dict):
            design_hints = self._merge_design_hints(design_hints, hints)
        return study_flags, meal_details, design_hints

    def _merge_llm_output(
        self,
        llm_data: Dict[str, Any],
        source_id: str,
        text: str,
        pk_values: List[PKValue],
        ci_values: List[CIValue],
        found_metrics: set,
        ambiguous_source: bool = False,
    ) -> tuple[List[PKValue], List[CIValue], set]:
        llm_pk_raw = llm_data.get("pk_values") or []
        llm_ci_raw = llm_data.get("ci_values") or []

        llm_pk_values: List[PKValue] = []
        for item in llm_pk_raw:
            try:
                pk_item = PKValue(**item)
            except Exception:
                continue
            if pk_item.value is None or not pk_item.name:
                continue
            self._ensure_llm_evidence(pk_item, source_id, text)
            self._add_warning(pk_item, "llm_extracted_requires_human_review")
            if ambiguous_source:
                pk_item.ambiguous_condition = True
                self._add_warning(pk_item, "ambiguous_condition")
            llm_pk_values.append(pk_item)

        for pk_item in llm_pk_values:
            existing_same = [pk for pk in pk_values if pk.name == pk_item.name and pk.value is not None]
            if any(abs(pk.value - pk_item.value) <= 1e-6 for pk in existing_same if pk.value is not None):
                for pk in existing_same:
                    if pk.value is None:
                        continue
                    if abs(pk.value - pk_item.value) <= 1e-6 and not pk.evidence and pk_item.evidence:
                        pk.evidence = pk_item.evidence
                        self._add_warning(pk, "llm_evidence_applied")
                        break
                continue
            if existing_same:
                for pk in existing_same:
                    self._add_warning(pk, "llm_conflict_with_regex")
                self._add_warning(pk_item, "llm_conflict_with_regex")
            pk_values.append(pk_item)
            found_metrics.add(pk_item.name)

        for item in llm_ci_raw:
            try:
                ci_item = CIValue(**item)
            except Exception:
                continue
            self._ensure_llm_ci_evidence(ci_item, source_id, text)
            if "llm_extracted_requires_human_review" not in ci_item.warnings:
                ci_item.warnings.append("llm_extracted_requires_human_review")
            if ambiguous_source:
                ci_item.ambiguous_condition = True
                if "ambiguous_condition" not in ci_item.warnings:
                    ci_item.warnings.append("ambiguous_condition")
            ci_values.append(ci_item)

        return pk_values, ci_values, found_metrics

    def _ensure_llm_evidence(self, pk_item: PKValue, source_id: str, text: str) -> None:
        if pk_item.evidence:
            pk_item.evidence = [self._normalize_llm_evidence(ev, source_id) for ev in pk_item.evidence]
            return
        snippet, span = self._find_value_snippet(text, pk_item.value)
        if snippet:
            context_tags = self._context_tags(snippet)
            evidence = self._build_evidence(
                source_id,
                snippet,
                context_tags,
                offset_start=span[0],
                offset_end=span[1],
                location="abstract_llm",
            )
            pk_item.evidence = [evidence]
        else:
            pk_item.evidence = [
                self._build_evidence(
                    source_id,
                    "LLM extracted value (snippet not found).",
                    None,
                    location="abstract_llm",
                )
            ]
        self._add_warning(pk_item, "llm_missing_evidence")

    def _ensure_llm_ci_evidence(self, ci_item: CIValue, source_id: str, text: str) -> None:
        if ci_item.evidence:
            ci_item.evidence = [self._normalize_llm_evidence(ev, source_id) for ev in ci_item.evidence]
            return
        snippet, span = self._find_value_snippet(text, ci_item.ci_low)
        if snippet:
            context_tags = self._context_tags(snippet)
            evidence = self._build_evidence(
                source_id,
                snippet,
                context_tags,
                offset_start=span[0],
                offset_end=span[1],
                location="abstract_llm",
            )
            ci_item.evidence = [evidence]
        else:
            ci_item.evidence = [
                self._build_evidence(
                    source_id,
                    "LLM extracted CI (snippet not found).",
                    None,
                    location="abstract_llm",
                )
            ]
        if "llm_missing_evidence" not in ci_item.warnings:
            ci_item.warnings.append("llm_missing_evidence")

    def _normalize_llm_evidence(self, ev: Evidence | Dict[str, Any], source_id: str) -> Evidence:
        if isinstance(ev, Evidence):
            payload = ev.model_dump()
        else:
            payload = dict(ev)
        payload.setdefault("source_id", source_id)
        if not payload.get("pmid_or_url"):
            payload["pmid_or_url"] = self._format_source_id(source_id)
        payload.setdefault("location", "abstract_llm")
        try:
            return Evidence(**payload)
        except Exception:
            return self._build_evidence(
                source_id,
                payload.get("excerpt") or payload.get("snippet") or "LLM evidence (invalid payload).",
                None,
                location="abstract_llm",
            )

    @staticmethod
    def _find_value_snippet(text: str, value: float | None) -> tuple[str, tuple[int, int]]:
        if value is None:
            return "", (0, 0)
        candidates = [str(value), f"{value:.2f}", f"{value:.1f}", f"{value:.0f}"]
        for cand in candidates:
            idx = text.find(cand)
            if idx != -1:
                return PKExtractor._make_snippet(text, idx, idx + len(cand)), (idx, idx + len(cand))
        return "", (0, 0)

    @staticmethod
    def _value_exists(entries: List[Tuple[float, int]], value: float) -> bool:
        return any(abs(existing - value) <= 1e-6 for existing, _ in entries)

    @staticmethod
    def _add_warning(pk: PKValue, warning: str) -> None:
        if pk.warnings is None:
            pk.warnings = []
        if warning not in pk.warnings:
            pk.warnings.append(warning)

    def _detect_conflicts(
        self, existing: List[PKValue], name: str, value: float, source_id: str
    ) -> Tuple[List[str], List[str]]:
        new_label = self._format_source_id(source_id)
        metric_entries = [pk for pk in existing if pk.name == name and pk.value is not None]
        distinct_values: List[float] = []
        for pk in metric_entries:
            if pk.value is None:
                continue
            if not any(abs(pk.value - v) <= 1e-6 for v in distinct_values):
                distinct_values.append(pk.value)
        if not any(abs(value - v) <= 1e-6 for v in distinct_values):
            distinct_values.append(value)

        if len(distinct_values) <= 1:
            return [], []

        conflict_sources = sorted(
            {
                self._source_label(pk)
                for pk in metric_entries
                if self._source_label(pk) is not None
            }
            | {new_label}
        )
        new_warnings = []
        for other in conflict_sources:
            if other == new_label:
                continue
            new_warnings.append(f"conflict_detected:{new_label}vs {other}")

        for pk in metric_entries:
            pk_source = self._source_label(pk)
            for other in conflict_sources:
                if other == pk_source:
                    continue
                self._add_warning(pk, f"conflict_detected:{pk_source}vs {other}")
            pk.conflict_sources = conflict_sources

        return conflict_sources, new_warnings

    @staticmethod
    def _source_label(pk: PKValue) -> str:
        if pk.evidence:
            ev = pk.evidence[0]
            return ev.pmid_or_url or ev.source_id or ev.pmid or ev.url or ev.source or "unknown"
        return "unknown"

    @staticmethod
    def _format_source_id(source_id: str) -> str:
        if source_id.startswith("http"):
            return source_id
        if source_id.startswith("PMID:"):
            return source_id
        if source_id.startswith("PMCID:"):
            pmc_id = source_id.replace("PMCID:", "")
            if pmc_id and not pmc_id.upper().startswith("PMC"):
                pmc_id = f"PMC{pmc_id}"
            return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/"
        return f"PMID:{source_id}"

    @staticmethod
    def _pmc_url(source_id: str) -> str:
        match = re.search(r"(\d+)", source_id)
        if not match:
            return ""
        pmc_id = match.group(1)
        if not pmc_id.upper().startswith("PMC"):
            pmc_id = f"PMC{pmc_id}"
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/"

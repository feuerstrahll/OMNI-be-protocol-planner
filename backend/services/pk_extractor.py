from __future__ import annotations

import re
from typing import Dict, List, Tuple

from backend.schemas import CIValue, Evidence, PKValue
from backend.services.utils import normalize_space, safe_float


class PKExtractor:
    def __init__(self, llm_client=None, pmc_fetcher=None) -> None:
        self.llm_client = llm_client
        self.pmc_fetcher = pmc_fetcher
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

    def extract(self, abstracts: Dict[str, str]) -> Tuple[List[PKValue], List[CIValue], List[str]]:
        """Extract PK/CI values collecting all matches, annotating conflicts with sources, and expanding/context-tagging snippets."""
        pk_values: List[PKValue] = []
        ci_values: List[CIValue] = []
        found_metrics = set()

        for source_id, text in abstracts.items():
            clean_text = normalize_space(text)
            if not clean_text:
                continue
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
                    evidence = self._build_evidence(source_id, snippet, context_tags)

                    warnings: List[str] = []
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
                if confidence_level != 0.90:
                    warnings.append("confidence_level_not_90")
                context_tags = self._context_tags(snippet)
                evidence = self._build_evidence(source_id, snippet, context_tags)
                ci_values.append(
                    CIValue(
                        param=param,
                        ci_low=ci_low,
                        ci_high=ci_high,
                        confidence_level=confidence_level,
                        gmr=gmr,
                        n=n_val,
                        design_hint=design_hint,
                        evidence=[evidence],
                        warnings=warnings,
                    )
                )

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
                pk_values.append(
                    PKValue(
                        name="CVintra",
                        value=cv_float,
                        unit="%",
                        evidence=[evidence],
                        warnings=["llm_extracted_requires_human_review"],
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
                        ci_values.append(
                            CIValue(
                                param=ci_param,
                                ci_low=ci_low_f,
                                ci_high=ci_high_f,
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
                                warnings=["llm_extracted_requires_human_review"],
                            )
                        )
                break

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
    def _build_evidence(source_id: str, snippet: str, context_tags: Dict[str, bool] | None = None) -> Evidence:
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
            location="abstract",
            context_tags=context_tags,
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
            "fasted": any(term in text for term in ["fast", "fasting", "empty stomach"]),
            "fed": any(term in text for term in ["fed", "food", "after meal", "postprandial"]),
            "human": any(term in text for term in ["subject", "volunteer", "patient", "human"]),
            "animal": any(term in text for term in ["rat", "dog", "rabbit", "animal", "mouse"]),
            "crossover": any(term in text for term in ["crossover", "cross-over", "2x2", "2×2"]),
            "log_transformed": any(term in text for term in ["log-transformed", "ln(", "log-scale"]),
        }

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

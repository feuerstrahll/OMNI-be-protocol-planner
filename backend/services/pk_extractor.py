from __future__ import annotations

import re
from typing import Dict, List, Tuple

from backend.schemas import CIValue, Evidence, PKValue
from backend.services.utils import normalize_space, safe_float


class PKExtractor:
    def __init__(self) -> None:
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
        pk_values: List[PKValue] = []
        ci_values: List[CIValue] = []
        found_metrics = set()

        for source_id, text in abstracts.items():
            clean_text = normalize_space(text)
            if not clean_text:
                continue
            design_hint = self._infer_design_hint(clean_text)
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
                    snippet = self._make_snippet(clean_text, match.start(), match.end())
                    evidence = self._build_evidence(source_id, snippet)
                    warnings: List[str] = []
                    if self._has_conflict(pk_values, metric_name, value):
                        warnings.append("conflict_detected")
                    pk_values.append(
                        PKValue(
                            name=metric_name,
                            value=value,
                            unit=unit,
                            evidence=[evidence],
                            warnings=warnings,
                        )
                    )
                    found_metrics.add(metric_name)
                    break

            for match in self.ci_pattern.finditer(clean_text):
                ci_low = safe_float(match.group("low"))
                ci_high = safe_float(match.group("high"))
                if ci_low is None or ci_high is None:
                    continue
                snippet = self._make_snippet(clean_text, match.start(), match.end(), window=140)
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
                evidence = self._build_evidence(source_id, snippet)
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

        auc_found = any(m in found_metrics for m in ["AUC", "AUC0-inf", "AUC0-t"])
        missing = []
        for m in ["Cmax", "AUC", "t1/2", "CVintra"]:
            if m == "AUC" and auc_found:
                continue
            if m not in found_metrics:
                missing.append(m)
        return pk_values, ci_values, missing

    @staticmethod
    def _make_snippet(text: str, start: int, end: int, window: int = 80) -> str:
        left = max(0, start - window)
        right = min(len(text), end + window)
        snippet = text[left:right]
        return snippet[:300]

    @staticmethod
    def _build_evidence(source_id: str, snippet: str) -> Evidence:
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
    def _has_conflict(existing: List[PKValue], name: str, value: float) -> bool:
        conflict = False
        for pk in existing:
            if pk.name == name and pk.value is not None and abs(pk.value - value) > 1e-6:
                conflict = True
                if pk.warnings is not None and "conflict_detected" not in pk.warnings:
                    pk.warnings.append("conflict_detected")
        return conflict

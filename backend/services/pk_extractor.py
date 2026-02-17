from __future__ import annotations

import re
from typing import Dict, List, Tuple

from backend.schemas import Evidence, NumericValue, PKValue
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
        }

    def extract(self, abstracts: Dict[str, str]) -> Tuple[List[PKValue], List[str]]:
        pk_values: List[PKValue] = []
        found_metrics = set()

        for source_id, text in abstracts.items():
            clean_text = normalize_space(text)
            if not clean_text:
                continue
            # Regex-based extraction from abstracts (MVP).
            for metric, pattern in self.patterns.items():
                for match in pattern.finditer(clean_text):
                    value = safe_float(match.group(2))
                    unit = match.group(3)
                    if value is None:
                        continue
                    metric_name = metric
                    if metric == "AUC":
                        label = (match.group(1) or "").lower()
                        if "inf" in label or "∞" in label:
                            metric_name = "AUC_inf"
                        elif "0-t" in label or "0t" in label:
                            metric_name = "AUC_last"
                    snippet = self._make_snippet(clean_text, match.start(), match.end())
                    source_is_pmc = source_id.startswith("PMCID:")
                    pmc_id = source_id.replace("PMCID:", "")
                    if pmc_id and not pmc_id.upper().startswith("PMC"):
                        pmc_id = f"PMC{pmc_id}"
                    evidence = Evidence(
                        source_type="PMID" if not source_is_pmc else "URL",
                        source=f"PMID:{source_id}" if not source_is_pmc else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/",
                        snippet=snippet,
                        context="Abstract (NCBI EFetch)",
                    )
                    pk_values.append(
                        PKValue(
                            metric=metric_name,
                            value=NumericValue(value=value, unit=unit, evidence=[evidence]),
                            confidence="medium",
                        )
                    )
                    found_metrics.add(metric_name)
                    break

        auc_found = any(m in found_metrics for m in ["AUC", "AUC_inf", "AUC_last"])
        missing = []
        for m in ["Cmax", "AUC", "t1/2", "CVintra"]:
            if m == "AUC" and auc_found:
                continue
            if m not in found_metrics:
                missing.append(m)
        return pk_values, missing

    @staticmethod
    def _make_snippet(text: str, start: int, end: int, window: int = 80) -> str:
        left = max(0, start - window)
        right = min(len(text), end + window)
        return text[left:right]

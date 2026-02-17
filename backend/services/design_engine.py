from __future__ import annotations

from typing import List, Optional

import yaml

from backend.schemas import CVInput, DesignReason, DesignResponse, PKExtractionResponse


class DesignEngine:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}

    def select_design(
        self,
        pk_json: PKExtractionResponse,
        cv_input: Optional[CVInput],
        nti: Optional[bool],
    ) -> DesignResponse:
        warnings: List[str] = []
        cv_for_design = 40.0

        if cv_input and cv_input.confirmed:
            cv_for_design = cv_input.cv.value
        else:
            cv_from_pk = self._cv_from_pk(pk_json)
            if cv_from_pk is not None:
                warnings.append(
                    "CVintra extracted but not confirmed. Using conservative default CV=40% for design suggestion."
                )
            else:
                warnings.append(
                    "CVintra not available. Using conservative default CV=40% for design suggestion."
                )

        rules = self.rules.get("rules", [])
        reasoning: List[DesignReason] = []

        if nti:
            nti_rule = next((r for r in rules if r.get("type") == "nti"), None)
            design = nti_rule.get("design") if nti_rule else "replicate with tighter BE limits"
            msg = nti_rule.get("message") if nti_rule else "NTI flag implies tighter BE limits and replicate design."
            reasoning.append(DesignReason(rule_id=nti_rule.get("id", "NTI"), message=msg))
            return DesignResponse(design=design, reasoning=reasoning, warnings=warnings)

        design, rule_id, msg = self._design_by_cv(rules, cv_for_design)
        reasoning.append(DesignReason(rule_id=rule_id, message=msg))

        return DesignResponse(design=design, reasoning=reasoning, warnings=warnings)

    @staticmethod
    def _cv_from_pk(pk_json: PKExtractionResponse) -> Optional[float]:
        for pk in pk_json.pk_values:
            if pk.metric == "CVintra":
                return pk.value.value
        return None

    @staticmethod
    def _design_by_cv(rules: List[dict], cv_value: float) -> tuple[str, str, str]:
        for rule in rules:
            if rule.get("type") != "cv_range":
                continue
            min_v = rule.get("min", None)
            max_v = rule.get("max", None)
            if min_v is not None and cv_value < float(min_v):
                continue
            if max_v is not None and cv_value > float(max_v):
                continue
            return rule.get("design"), rule.get("id"), rule.get("message")
        return "2x2 crossover", "DEFAULT", "Default to 2x2 crossover when no rule matches."

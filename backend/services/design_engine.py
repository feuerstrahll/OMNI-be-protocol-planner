from __future__ import annotations

from typing import List, Optional, Tuple

import yaml

from backend.schemas import CVInput, DesignReason, DesignResponse, PKExtractionResponse


class DesignEngine:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}
        self._rules = self._normalize_rules(self.rules.get("rules", []))
        self._default_cv = float(self.rules.get("defaults", {}).get("cv", 40))

    def select_design(
        self,
        pk_json: PKExtractionResponse,
        cv_input: Optional[CVInput],
        nti: Optional[bool],
    ) -> DesignResponse:
        warnings: List[str] = []
        required_inputs_missing: List[str] = []

        cv_for_design, cv_missing, cv_notes = self._resolve_cv_for_design(pk_json, cv_input)
        required_inputs_missing.extend(cv_missing)
        warnings.extend(cv_notes)
        if cv_for_design is None:
            cv_for_design = self._default_cv
            warnings.append(f"Using default CV={self._default_cv}% for design suggestion.")

        if nti is None:
            required_inputs_missing.append("NTI flag")

        reasoning: List[DesignReason] = []

        matched = self._match_rule(self._rules, cv_for_design, nti)
        if not matched:
            matched = self._default_rule(self._rules)

        design = matched.get("design", "2x2 crossover")
        rule_id = matched.get("id", "DEFAULT")
        message = matched.get("message") or "Default to 2x2 crossover when no rule matches."
        reasoning.append(DesignReason(rule_id=rule_id, message=message))

        return DesignResponse(
            design=design,
            reasoning=reasoning,
            reasoning_rule_id=rule_id,
            reasoning_text=message,
            required_inputs_missing=required_inputs_missing,
            warnings=warnings,
        )

    @staticmethod
    def _cv_from_pk(pk_json: PKExtractionResponse) -> Optional[float]:
        for pk in pk_json.pk_values:
            if pk.name == "CVintra":
                return pk.value
        return None

    @staticmethod
    def _normalize_rules(raw_rules: List[dict]) -> List[dict]:
        normalized: List[dict] = []
        total = len(raw_rules)
        for idx, rule in enumerate(raw_rules):
            when = dict(rule.get("when") or {})
            if "when" not in rule and "priority" not in rule:
                rule_type = rule.get("type")
                if rule_type == "nti":
                    when["nti"] = True
                elif rule_type == "cv_range":
                    if "min" in rule:
                        when["cv_min"] = rule.get("min")
                    if "max" in rule:
                        when["cv_max"] = rule.get("max")
            priority = int(rule.get("priority", total - idx))
            normalized.append(
                {
                    "id": rule.get("id", f"RULE_{idx + 1}"),
                    "design": rule.get("design", "2x2 crossover"),
                    "message": rule.get("message", ""),
                    "when": when,
                    "priority": priority,
                    "order": idx,
                }
            )
        normalized.sort(key=lambda r: (-r["priority"], r["order"]))
        return normalized

    @staticmethod
    def _match_rule(rules: List[dict], cv_value: float, nti: Optional[bool]) -> Optional[dict]:
        for rule in rules:
            if DesignEngine._rule_matches(rule.get("when") or {}, cv_value, nti):
                return rule
        return None

    @staticmethod
    def _default_rule(rules: List[dict]) -> dict:
        for rule in reversed(rules):
            if not rule.get("when"):
                return rule
        return {"id": "DEFAULT", "design": "2x2 crossover", "message": "Default to 2x2 crossover."}

    @staticmethod
    def _rule_matches(when: dict, cv_value: float, nti: Optional[bool]) -> bool:
        if not when:
            return True
        if "nti" in when:
            if nti is None:
                return False
            if bool(nti) != bool(when.get("nti")):
                return False
        if "cv_min" in when:
            if cv_value is None:
                return False
            if cv_value < float(when.get("cv_min")):
                return False
        if "cv_max" in when:
            if cv_value is None:
                return False
            if cv_value > float(when.get("cv_max")):
                return False
        return True

    def _resolve_cv_for_design(
        self, pk_json: PKExtractionResponse, cv_input: Optional[CVInput]
    ) -> Tuple[Optional[float], List[str], List[str]]:
        missing: List[str] = []
        warnings: List[str] = []
        if cv_input and cv_input.cv and cv_input.cv.value is not None:
            if cv_input.confirmed:
                return cv_input.cv.value, missing, warnings
            missing.append("CVintra confirmation")
            warnings.append("CVintra provided but not confirmed.")
            return cv_input.cv.value, missing, warnings

        cv_from_pk = self._cv_from_pk(pk_json)
        if cv_from_pk is not None:
            missing.append("CVintra confirmation")
            warnings.append("CVintra extracted but not confirmed.")
            return cv_from_pk, missing, warnings

        missing.append("CVintra")
        warnings.append("CVintra not available.")
        return None, missing, warnings

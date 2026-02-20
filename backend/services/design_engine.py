from __future__ import annotations

from typing import List, Optional, Tuple

import yaml

from backend.schemas import CVInput, DesignReason, DesignResponse, PKExtractionResponse


class DesignEngine:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}
        self._legacy_rules = self.rules.get("rules", [])
        self._use_new_rules = bool(self.rules.get("baseline_design") or self.rules.get("drivers"))
        self._rules = self._normalize_rules(self._legacy_rules) if self._legacy_rules else []
        self._default_cv = float(self.rules.get("defaults", {}).get("cv", 40))
        self._baseline_design = str(self.rules.get("baseline_design", "2x2 crossover"))

    def select_design(
        self,
        pk_json: PKExtractionResponse,
        cv_input: Optional[CVInput],
        nti: Optional[bool],
    ) -> DesignResponse:
        if self._use_new_rules:
            return self._select_design_new(pk_json, cv_input, nti)
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

    def _select_design_new(
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

        t_half = self._pk_value(pk_json, "t1/2")
        if t_half is None:
            required_inputs_missing.append("t1/2")

        if nti is None:
            required_inputs_missing.append("NTI flag")

        drivers = self.rules.get("drivers") or {}
        classification = self.rules.get("classification_rules") or {}

        cv_threshold = self._extract_threshold(classification.get("HVD", {}).get("condition")) or 30.0
        t_half_threshold = self._extract_threshold(classification.get("Long_t_half", {}).get("condition")) or 72.0

        matched_driver = None
        for key in drivers.keys():
            if key == "high_intra_subject_variability" and cv_for_design is not None:
                if float(cv_for_design) >= cv_threshold:
                    matched_driver = key
                    break
            if key == "narrow_therapeutic_index" and nti is True:
                matched_driver = key
                break
            if key == "long_half_life" and t_half is not None:
                if float(t_half) >= t_half_threshold:
                    matched_driver = key
                    break
            if key in ("carryover_risk", "multiple_formulations_or_conditions", "ethical_steady_state"):
                required_inputs_missing.append(key.replace("_", " "))

        reasoning: List[DesignReason] = []
        if matched_driver:
            driver = drivers.get(matched_driver, {})
            design = str(driver.get("recommended_design") or self._baseline_design)
            message = str(driver.get("reason") or driver.get("title") or "")
            reasoning.append(DesignReason(rule_id=matched_driver, message=message or "Rule-based design selection."))
            return DesignResponse(
                design=design,
                reasoning=reasoning,
                reasoning_rule_id=matched_driver,
                reasoning_text=message or "Rule-based design selection.",
                required_inputs_missing=required_inputs_missing,
                warnings=warnings,
            )

        reasoning.append(
            DesignReason(
                rule_id="baseline_design",
                message=f"Default baseline design: {self._baseline_design}.",
            )
        )
        return DesignResponse(
            design=self._baseline_design,
            reasoning=reasoning,
            reasoning_rule_id="baseline_design",
            reasoning_text=f"Default baseline design: {self._baseline_design}.",
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
    def _pk_value(pk_json: PKExtractionResponse, name: str) -> Optional[float]:
        for pk in pk_json.pk_values:
            if pk.name == name and pk.value is not None:
                return pk.value
        return None

    @staticmethod
    def _extract_threshold(condition: Optional[str]) -> Optional[float]:
        if not condition:
            return None
        import re

        match = re.search(r"([0-9]+(?:\\.[0-9]+)?)", condition)
        if not match:
            return None
        try:
            return float(match.group(1))
        except Exception:
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

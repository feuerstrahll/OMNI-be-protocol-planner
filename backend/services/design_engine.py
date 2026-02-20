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
        used_default = False
        if cv_for_design is None:
            used_default = True
            cv_for_design = self._default_cv
            warnings.append(f"Using default CV={self._default_cv}% for design suggestion.")

        t_half = self._extract_t_half(pk_json)
        if t_half is None:
            required_inputs_missing.append("t1/2")

        if nti is None:
            required_inputs_missing.append("NTI flag")

        classification = self.rules.get("classification_rules") or {}

        reasoning: List[DesignReason] = []

        # REG-006b override: very long half-life forces parallel design
        if t_half is not None and float(t_half) >= 150:
            message = "T1/2 >= 150h. Parallel design strongly recommended."
            reasoning.append(DesignReason(rule_id="REG-006b", message=message))
            return DesignResponse(
                design="parallel",
                reasoning=reasoning,
                reasoning_rule_id="REG-006b",
                reasoning_text=message,
                required_inputs_missing=required_inputs_missing,
                warnings=warnings,
            )

        rsabe_cfg = classification.get("RSABE", {}) if isinstance(classification, dict) else {}
        hvd_cfg = classification.get("HVD", {}) if isinstance(classification, dict) else {}

        rsabe_threshold = self._extract_threshold(rsabe_cfg.get("condition")) or 0.50
        hvd_threshold = self._extract_threshold(hvd_cfg.get("condition")) or 30.0

        if not used_default and cv_for_design is not None:
            if self._cv_meets_threshold(cv_for_design, rsabe_threshold):
                design = str(rsabe_cfg.get("design") or "4-way_replicate")
                message = str(
                    rsabe_cfg.get("note") or "Reference-Scaled ABE required (EAEU Decision 85)."
                )
                reasoning.append(DesignReason(rule_id="RSABE", message=message))
                return DesignResponse(
                    design=design,
                    reasoning=reasoning,
                    reasoning_rule_id="RSABE",
                    reasoning_text=message,
                    required_inputs_missing=required_inputs_missing,
                    warnings=warnings,
                )

            if self._cv_meets_threshold(cv_for_design, hvd_threshold):
                design = str(hvd_cfg.get("design") or "replicate")
                message = str(
                    hvd_cfg.get("note")
                    or "Highly variable drug (CVintra >= 30%): replicate design recommended."
                )
                reasoning.append(DesignReason(rule_id="HVD", message=message))
                return DesignResponse(
                    design=design,
                    reasoning=reasoning,
                    reasoning_rule_id="HVD",
                    reasoning_text=message,
                    required_inputs_missing=required_inputs_missing,
                    warnings=warnings,
                )

        baseline_design = self._normalize_baseline(self._baseline_design)
        reasoning.append(
            DesignReason(
                rule_id="baseline_design",
                message=f"Default baseline design: {baseline_design}.",
            )
        )
        return DesignResponse(
            design=baseline_design,
            reasoning=reasoning,
            reasoning_rule_id="baseline_design",
            reasoning_text=f"Default baseline design: {baseline_design}.",
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
    def _extract_t_half(pk_json: PKExtractionResponse) -> Optional[float]:
        # Legacy flat PK values
        flat = DesignEngine._pk_value(pk_json, "t1/2")
        if flat is not None:
            return flat
        flat = DesignEngine._pk_value(pk_json, "t_half")
        if flat is not None:
            return flat

        # Hierarchical study_arms: look for t_half_mean
        arms = getattr(pk_json, "study_arms", None)
        if not arms:
            return None
        values: List[float] = []
        for arm in arms:
            val = None
            if isinstance(arm, dict):
                val = arm.get("t_half_mean")
            else:
                val = getattr(arm, "t_half_mean", None)
            if val is None:
                continue
            try:
                values.append(float(val))
            except Exception:
                continue
        return max(values) if values else None

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
    def _cv_meets_threshold(cv_value: float, threshold: float) -> bool:
        if threshold <= 1.0:
            return (cv_value / 100.0) >= threshold
        return cv_value >= threshold

    @staticmethod
    def _normalize_baseline(baseline: str) -> str:
        text = (baseline or "").strip()
        if "2x2" in text and "crossover" in text and "_" not in text:
            return "2x2_crossover"
        return text or "2x2_crossover"

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

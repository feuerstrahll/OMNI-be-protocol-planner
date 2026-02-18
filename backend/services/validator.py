from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import yaml

from backend.schemas import PKValue, ValidationIssue


class PKValidator:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}
        self.metric_aliases = {
            "AUC0-t": "AUC",
            "AUC0-inf": "AUC",
            "AUC_inf": "AUC",
            "AUC_last": "AUC",
        }
        self.normalization = self._build_normalization()

    def validate(self, pk_values: List[PKValue]) -> List[ValidationIssue]:
        issues, _ = self.validate_with_warnings(pk_values)
        return issues

    def validate_with_warnings(self, pk_values: List[PKValue]) -> Tuple[List[ValidationIssue], List[str]]:
        issues: List[ValidationIssue] = []
        global_warnings: List[str] = []
        metric_rules: Dict[str, Dict] = self.rules.get("metrics", {})

        for pk in pk_values:
            pk.warnings = pk.warnings or []
            rules = metric_rules.get(pk.name, {})
            unit_allowed = rules.get("units", [])
            min_val = rules.get("min", None)
            max_val = rules.get("max", None)

            canonical_unit = self._canonical_unit(pk.unit) if pk.unit else None
            canonical_allowed = [self._canonical_unit(u) for u in unit_allowed]
            if pk.unit and unit_allowed and canonical_unit not in canonical_allowed:
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="WARN",
                        message=f"Unexpected unit '{pk.unit}' for {pk.name}. Allowed: {unit_allowed}",
                    )
                )
                if "unit_not_allowed" not in pk.warnings:
                    pk.warnings.append("unit_not_allowed")
            if not pk.unit:
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="WARN",
                        message=f"Missing unit for {pk.name}.",
                    )
                )
                if "missing_unit" not in pk.warnings:
                    pk.warnings.append("missing_unit")

            if pk.value is None:
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="ERROR",
                        message=f"Missing value for {pk.name}.",
                    )
                )
                if "missing_value" not in pk.warnings:
                    pk.warnings.append("missing_value")
                continue

            normalized = self._normalize_value(pk.name, pk.value, pk.unit)
            if normalized:
                pk.normalized_value, pk.normalized_unit = normalized
            elif pk.unit:
                if "unit_normalization_failed" not in pk.warnings:
                    pk.warnings.append("unit_normalization_failed")

            if pk.value <= 0:
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="ERROR",
                        message=f"Non-positive value for {pk.name}.",
                    )
                )

            check_value = pk.normalized_value if pk.normalized_value is not None else pk.value
            if min_val is not None and check_value is not None and check_value < float(min_val):
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="WARN",
                        message=f"{pk.name} below expected minimum ({min_val}).",
                    )
                )
                if "out_of_range" not in pk.warnings:
                    pk.warnings.append("out_of_range")

            if max_val is not None and check_value is not None and check_value > float(max_val):
                issues.append(
                    ValidationIssue(
                        metric=pk.name,
                        severity="WARN",
                        message=f"{pk.name} above expected maximum ({max_val}).",
                    )
                )
                if "out_of_range" not in pk.warnings:
                    pk.warnings.append("out_of_range")

        self._detect_conflicts(pk_values, issues, global_warnings)
        return issues, global_warnings

    def _detect_conflicts(
        self,
        pk_values: List[PKValue],
        issues: List[ValidationIssue],
        global_warnings: List[str],
    ) -> None:
        by_name: Dict[str, List[float]] = {}
        for pk in pk_values:
            val = pk.normalized_value if pk.normalized_value is not None else pk.value
            if val is None:
                continue
            by_name.setdefault(pk.name, []).append(val)
        for name, values in by_name.items():
            if len(values) < 2:
                continue
            vmin = min(values)
            vmax = max(values)
            if vmin <= 0:
                continue
            rel_diff = (vmax - vmin) / vmin
            if rel_diff > 0.1:
                global_warnings.append(f"conflict_detected:{name}")
                for pk in pk_values:
                    if pk.name == name and "conflict_detected" not in pk.warnings:
                        pk.warnings.append("conflict_detected")
                issues.append(
                    ValidationIssue(
                        metric=name,
                        severity="WARN",
                        message=f"Conflicting values detected for {name}.",
                    )
                )

    def _normalize_value(self, name: str, value: float, unit: Optional[str]) -> Optional[Tuple[float, str]]:
        if unit is None:
            return None
        group = self.metric_aliases.get(name, name)
        rules = self.normalization.get(group)
        if not rules:
            return None
        canonical_unit = self._canonical_unit(unit)
        factor = rules["factors"].get(canonical_unit)
        if factor is None:
            return None
        return value * factor, rules["unit"]

    @staticmethod
    def _canonical_unit(unit: Optional[str]) -> Optional[str]:
        if unit is None:
            return None
        u = unit.strip()
        u = u.replace("μ", "µ")
        u = u.replace("·", "*")
        u = u.replace("hours", "h")
        u = u.replace("hrs", "h")
        u = u.replace("hr", "h")
        u = u.replace(" ", "")
        u = u.lower()
        return u

    @staticmethod
    def _build_normalization() -> Dict[str, Dict[str, Dict[str, float] | str]]:
        return {
            "Cmax": {
                "unit": "ng/mL",
                "factors": {
                    "ng/ml": 1.0,
                    "mg/l": 1000.0,
                    "µg/l": 1.0,
                    "ug/l": 1.0,
                    "ng/l": 0.001,
                    "mg/ml": 1_000_000.0,
                    "µg/ml": 1000.0,
                    "ug/ml": 1000.0,
                },
            },
            "AUC": {
                "unit": "ng*h/mL",
                "factors": {
                    "ng*h/ml": 1.0,
                    "ng*hr/ml": 1.0,
                    "mg*h/l": 1000.0,
                    "µg*h/l": 1.0,
                    "ug*h/l": 1.0,
                    "ng*h/l": 0.001,
                },
            },
            "t1/2": {
                "unit": "h",
                "factors": {
                    "h": 1.0,
                },
            },
            "Tmax": {
                "unit": "h",
                "factors": {
                    "h": 1.0,
                    "min": 1.0 / 60.0,
                },
            },
            "CVintra": {
                "unit": "%",
                "factors": {
                    "%": 1.0,
                },
            },
            "lambda_z": {
                "unit": "1/h",
                "factors": {
                    "1/h": 1.0,
                    "h^-1": 1.0,
                    "hr^-1": 1.0,
                },
            },
        }

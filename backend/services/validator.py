from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import yaml

from backend.schemas import CIValue, PKValue, ValidationIssue


class PKValidator:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}
        self.metric_aliases = {
            "AUC0-t": "AUC",
            "AUC0-inf": "AUC",
            "AUC_inf": "AUC",
            "AUC_last": "AUC",
            "t1/2": "t_half",
            "CVintra": "CV",
        }
        if "metrics" in self.rules:
            self.metric_rules = self.rules.get("metrics", {})
            self.normalization = self._build_normalization()
            self.warning_rules = []
        else:
            self.metric_rules = self._build_metric_rules_from_new(self.rules)
            self.normalization = self._build_normalization_from_rules(self.rules)
            self.warning_rules = self.rules.get("warnings") or []

    def validate(self, pk_values: List[PKValue], ci_values: Optional[List[CIValue]] = None) -> List[ValidationIssue]:
        issues, _ = self.validate_with_warnings(pk_values, ci_values)
        return issues

    def validate_with_warnings(
        self, pk_values: List[PKValue], ci_values: Optional[List[CIValue]] = None
    ) -> Tuple[List[ValidationIssue], List[str]]:
        issues: List[ValidationIssue] = []
        global_warnings: List[str] = []
        metric_rules: Dict[str, Dict] = self.metric_rules

        for pk in pk_values:
            pk.warnings = pk.warnings or []
            rules = metric_rules.get(self._resolve_metric_name(pk.name), {})
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

            self._apply_warning_rules(pk)

        if ci_values:
            self._check_ci_vs_cv(pk_values, ci_values, issues, global_warnings)
        self._detect_conflicts(pk_values, issues, global_warnings)
        return issues, global_warnings

    def _check_ci_vs_cv(
        self,
        pk_values: List[PKValue],
        ci_values: List[CIValue],
        issues: List[ValidationIssue],
        global_warnings: List[str],
    ) -> None:
        cv_value = next((pk.value for pk in pk_values if pk.name == "CVintra" and pk.value is not None), None)
        if cv_value is None:
            return
        try:
            cv_ratio = float(cv_value) / 100.0
        except Exception:
            return
        if cv_ratio <= 0:
            return

        for ci in ci_values:
            if ci.n is None or ci.ci_low is None or ci.ci_high is None:
                continue
            try:
                n = int(ci.n)
                ci_low = float(ci.ci_low)
                ci_high = float(ci.ci_high)
            except Exception:
                continue
            if n <= 0 or ci_low <= 0 or ci_high <= 0:
                continue
            if ci_low >= ci_high:
                continue

            ci_low_ratio = ci_low / 100.0 if ci.ci_type == "percent" else ci_low
            ci_high_ratio = ci_high / 100.0 if ci.ci_type == "percent" else ci_high

            sd_log = math.sqrt(math.log(1 + cv_ratio**2))
            se = math.sqrt(2.0 / n) * sd_log
            half_width_expected = 1.645 * se
            width_expected = 2 * half_width_expected
            width_actual = abs(math.log(ci_high_ratio) - math.log(ci_low_ratio))
            if width_expected <= 0:
                continue
            rel_diff = abs(width_actual - width_expected) / width_expected
            if rel_diff > 0.5:
                message = (
                    f"CI width conflicts with CV={cv_value}% and n={n}: "
                    f"expected ~{width_expected:.2f} (log-scale), got {width_actual:.2f}."
                )
                issues.append(
                    ValidationIssue(
                        metric=f"CI_{ci.param}",
                        severity="WARN",
                        message=message,
                    )
                )
                global_warnings.append("conflict_detected:ci_vs_cv")

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

    def _build_metric_rules_from_new(self, rules: Dict) -> Dict[str, Dict]:
        metric_rules: Dict[str, Dict] = {}
        units = rules.get("units") or {}
        ranges = rules.get("ranges") or {}
        for metric, unit_list in units.items():
            metric_rules.setdefault(metric, {})["units"] = unit_list
        for metric, rng in ranges.items():
            metric_rules.setdefault(metric, {})["min"] = rng.get("min")
            metric_rules.setdefault(metric, {})["max"] = rng.get("max")
        return metric_rules

    def _build_normalization_from_rules(self, rules: Dict) -> Dict[str, Dict[str, Dict[str, float] | str]]:
        units = rules.get("units") or {}
        conversions = rules.get("conversions") or {}
        normalization: Dict[str, Dict[str, Dict[str, float] | str]] = {}
        for metric, unit_list in units.items():
            if not unit_list:
                continue
            canonical_unit = unit_list[0]
            canonical_key = self._canonical_unit(canonical_unit)
            factors = {canonical_key: 1.0}
            for conv_key, factor in conversions.items():
                if "_to_" not in conv_key:
                    continue
                from_unit, to_unit = conv_key.split("_to_", 1)
                if self._canonical_unit(to_unit) == canonical_key:
                    factors[self._canonical_unit(from_unit)] = float(factor)
            normalization[metric] = {
                "unit": canonical_unit,
                "factors": factors,
            }
        return normalization

    def _resolve_metric_name(self, name: str) -> str:
        if name in self.metric_rules:
            return name
        alias = self.metric_aliases.get(name)
        return alias if alias in self.metric_rules else name

    def _apply_warning_rules(self, pk: PKValue) -> None:
        if not self.warning_rules:
            return
        for rule in self.warning_rules:
            rule_text = str(rule)
            if rule_text.startswith("t_half") and pk.name in ("t1/2", "t_half") and pk.value is not None:
                if pk.value > 200:
                    self._add_warning(pk, "t_half_gt_200h")
            if rule_text.startswith("CV") and pk.name == "CVintra" and pk.value is not None:
                if pk.value > 60:
                    self._add_warning(pk, "cv_gt_60")

    @staticmethod
    def _add_warning(pk: PKValue, warning: str) -> None:
        if pk.warnings is None:
            pk.warnings = []
        if warning not in pk.warnings:
            pk.warnings.append(warning)

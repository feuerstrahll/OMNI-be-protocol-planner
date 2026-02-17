from __future__ import annotations

from typing import Dict, List

import yaml

from backend.schemas import PKValue, ValidationIssue


class PKValidator:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}

    def validate(self, pk_values: List[PKValue]) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        metric_rules: Dict[str, Dict] = self.rules.get("metrics", {})

        for pk in pk_values:
            rules = metric_rules.get(pk.metric, {})
            unit_allowed = rules.get("units", [])
            min_val = rules.get("min", None)
            max_val = rules.get("max", None)

            if pk.value.unit and unit_allowed and pk.value.unit not in unit_allowed:
                issues.append(
                    ValidationIssue(
                        metric=pk.metric,
                        severity="WARN",
                        message=f"Unexpected unit '{pk.value.unit}' for {pk.metric}. Allowed: {unit_allowed}",
                    )
                )
            if not pk.value.unit:
                issues.append(
                    ValidationIssue(
                        metric=pk.metric,
                        severity="WARN",
                        message=f"Missing unit for {pk.metric}.",
                    )
                )

            if pk.value.value <= 0:
                issues.append(
                    ValidationIssue(
                        metric=pk.metric,
                        severity="ERROR",
                        message=f"Non-positive value for {pk.metric}.",
                    )
                )

            if min_val is not None and pk.value.value < float(min_val):
                issues.append(
                    ValidationIssue(
                        metric=pk.metric,
                        severity="WARN",
                        message=f"{pk.metric} below expected minimum ({min_val}).",
                    )
                )

            if max_val is not None and pk.value.value > float(max_val):
                issues.append(
                    ValidationIssue(
                        metric=pk.metric,
                        severity="WARN",
                        message=f"{pk.metric} above expected maximum ({max_val}).",
                    )
                )

        return issues

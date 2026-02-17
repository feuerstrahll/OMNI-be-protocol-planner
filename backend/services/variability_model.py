from __future__ import annotations

from typing import List, Tuple

import yaml

from backend.schemas import CVRange, NumericValue, VariabilityInput, VariabilityResponse


class VariabilityModel:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}

    def estimate(self, data: VariabilityInput) -> VariabilityResponse:
        drivers: List[str] = []
        warnings: List[str] = []

        base_low, base_high = self._base_range(data.bcs_class)
        drivers.append(f"Base range from BCS class: {data.bcs_class or 'unknown'}")

        low, high = base_low, base_high

        # Rule-based adjustments from known drivers.
        if data.logp is not None:
            if data.logp >= 4:
                low, high = low + 10, high + 15
                drivers.append("High logP (>=4) increases variability")
            elif data.logp >= 3:
                low, high = low + 5, high + 10
                drivers.append("Moderate logP (>=3) increases variability")

        if data.t_half and data.t_half.value >= 24:
            low, high = low + 5, high + 10
            drivers.append("Long half-life (>=24 h) increases variability")

        if data.first_pass:
            if data.first_pass == "high":
                low, high = low + 10, high + 15
                drivers.append("High first-pass effect increases variability")
            elif data.first_pass == "medium":
                low, high = low + 5, high + 8
                drivers.append("Medium first-pass effect increases variability")

        if data.cyp_involvement:
            if data.cyp_involvement == "high":
                low, high = low + 10, high + 15
                drivers.append("High CYP involvement increases variability")
            elif data.cyp_involvement == "medium":
                low, high = low + 5, high + 8
                drivers.append("Medium CYP involvement increases variability")

        if data.nti:
            drivers.append("NTI flag present; consider conservative range")

        low = max(15, min(low, 80))
        high = max(low + 5, min(high, 90))
        mode = (low + high) / 2

        confidence = self._confidence(data)
        if confidence == "low":
            warnings.append("Limited features provided; CV range is conservative.")

        evidence = [
            {
                "source_type": "URL",
                "source": "calc://variability_rules",
                "snippet": "; ".join(drivers),
                "context": "Rule-based CV range estimate",
            }
        ]

        return VariabilityResponse(
            cv_range=CVRange(
                low=NumericValue(value=float(low), unit="%", evidence=evidence),
                high=NumericValue(value=float(high), unit="%", evidence=evidence),
                mode=NumericValue(value=float(mode), unit="%", evidence=evidence),
            ),
            drivers=drivers,
            confidence=confidence,
            warnings=warnings,
        )

    def _base_range(self, bcs_class: int | None) -> Tuple[int, int]:
        base = self.rules.get("base", {})
        if bcs_class:
            mapping = base.get("bcs", {})
            if str(bcs_class) in mapping:
                return tuple(mapping[str(bcs_class)])
        return tuple(base.get("default", [30, 50]))

    @staticmethod
    def _confidence(data: VariabilityInput) -> str:
        known = sum(
            [
                data.bcs_class is not None,
                data.logp is not None,
                data.t_half is not None,
                data.first_pass is not None,
                data.cyp_involvement is not None,
                data.nti is not None,
            ]
        )
        if known >= 4:
            return "high"
        if known >= 2:
            return "medium"
        return "low"

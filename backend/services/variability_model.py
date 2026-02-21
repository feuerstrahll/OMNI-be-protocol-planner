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

        if "baseline_CV_range" in self.rules:
            base_low, base_high = self._baseline_range()
            drivers.append("Base range from baseline_CV_range")
            low, high = base_low, base_high
            low, high = self._apply_biologist_drivers(data, low, high, drivers)
        else:
            base_low, base_high = self._base_range(data.bcs_class)
            drivers.append(f"Base range from BCS class: {data.bcs_class or 'unknown'}")
            low, high = base_low, base_high

            # Legacy rule-based adjustments from known drivers.
            if data.logp is not None:
                if data.logp >= 4:
                    low, high = low + 10, high + 15
                    drivers.append("High logP (>=4) increases variability")
                elif data.logp >= 3:
                    low, high = low + 5, high + 10
                    drivers.append("Moderate logP (>=3) increases variability")

            if data.t_half is not None and data.t_half >= 24:
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
        """Диапазон CV из правил (base.bcs / base.default). При пустом YAML или ошибке — (30, 50)."""
        fallback: Tuple[int, int] = (30, 50)
        base = self.rules.get("base") or {}
        try:
            if bcs_class is not None:
                mapping = base.get("bcs") or {}
                pair = mapping.get(str(bcs_class))
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    return (int(pair[0]), int(pair[1]))
            default = base.get("default", [30, 50])
            if isinstance(default, (list, tuple)) and len(default) >= 2:
                return (int(default[0]), int(default[1]))
        except (TypeError, ValueError, IndexError):
            pass
        return fallback

    def _baseline_range(self) -> Tuple[int, int]:
        baseline = self.rules.get("baseline_CV_range") or [30, 50]
        if isinstance(baseline, list) and len(baseline) >= 2:
            return int(baseline[0]), int(baseline[1])
        return 30, 50

    def _apply_biologist_drivers(
        self,
        data: VariabilityInput,
        low: int,
        high: int,
        drivers: List[str],
    ) -> Tuple[int, int]:
        cfg = self.rules.get("drivers") or {}

        def _apply(driver_key: str, condition: bool, lo: int, hi: int) -> Tuple[int, int]:
            if not condition or driver_key not in cfg:
                return lo, hi
            add = cfg.get(driver_key, {}).get("add_range") or [0, 0]
            if isinstance(add, list) and len(add) >= 2:
                lo = int(lo + float(add[0]))
                hi = int(hi + float(add[1]))
            mech = cfg.get(driver_key, {}).get("mechanism")
            if mech:
                drivers.append(mech)
            return lo, hi

        low, high = _apply("BCS_class_II_or_IV", data.bcs_class in (2, 4), low, high)
        low, high = _apply("strong_first_pass_metabolism", data.first_pass == "high", low, high)
        low, high = _apply("CYP_polymorphic_metabolism", data.cyp_involvement == "high", low, high)
        low, high = _apply("food_effect_present", bool(data.pk_json and data.pk_json.study_condition == "fed"), low, high)
        low, high = _apply("modified_release", False, low, high)

        if data.nti:
            drivers.append("NTI flag present; consider conservative range")

        return low, high

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

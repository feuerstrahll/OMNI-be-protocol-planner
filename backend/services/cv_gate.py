from __future__ import annotations

import math
import os
from typing import List, Tuple

from backend.schemas import CIValue, CVInfo, OpenQuestion, PKExtractionResponse, PKValue, VariabilityInput
from backend.services.powertost_runner import health as powertost_health
from backend.services.powertost_runner import run_cvfromci
from backend.services.variability_model import VariabilityModel


def select_cv_info(
    pk_json: PKExtractionResponse,
    ci_values: List[CIValue],
    manual_cv: float | None,
    cv_confirmed: bool,
    variability_model: VariabilityModel,
    use_fallback: bool = False,
) -> Tuple[CVInfo, List[OpenQuestion]]:
    open_questions: List[OpenQuestion] = []

    if manual_cv is not None:
        return (
            CVInfo(
                value=float(manual_cv),
                source="manual",
                cv_source="manual",
                confidence="medium",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=[
                    {
                        "source_type": "URL",
                        "source": "manual://user",
                        "snippet": "User input",
                        "context": "Manual CV input",
                    }
                ],
                warnings=[],
            ),
            open_questions,
        )

    reported = _find_reported_cv(pk_json.pk_values)
    if reported:
        return (
            CVInfo(
                value=reported.value,
                source="reported",
                cv_source="reported",
                parameter=None,
                confidence="medium",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=reported.evidence,
                warnings=reported.warnings or [],
            ),
            open_questions,
        )

    ci_candidate = _select_ci_candidate(ci_values)
    if ci_candidate:
        cv_info, cv_questions = _derive_from_ci(ci_candidate, cv_confirmed, use_fallback)
        open_questions.extend(cv_questions)
        return cv_info, open_questions

    # Range fallback
    range_info = variability_model.estimate(
        VariabilityInput(
            inn=pk_json.inn,
            pk_json=pk_json,
        )
    )
    cv_mode = range_info.cv_range.mode.value if range_info.cv_range.mode else None
    cv_info = CVInfo(
        value=cv_mode,
        source="range",
        cv_source="range",
        confidence=range_info.confidence,
        requires_human_confirm=True,
        confirmed_by_user=bool(cv_confirmed),
        evidence=[],
        warnings=range_info.warnings or [],
        range_low=range_info.cv_range.low.value,
        range_high=range_info.cv_range.high.value,
        range_mode=cv_mode,
        range_drivers=range_info.drivers,
        range_confidence=range_info.confidence,
    )
    return cv_info, open_questions


def _find_reported_cv(pk_values: List[PKValue]) -> PKValue | None:
    for pk in pk_values:
        if pk.name != "CVintra" or pk.value is None:
            continue
        if 5 <= pk.value <= 200:
            return pk
        if pk.warnings is not None and "cv_out_of_range" not in pk.warnings:
            pk.warnings.append("cv_out_of_range")
    return None


def _select_ci_candidate(ci_values: List[CIValue]) -> CIValue | None:
    for ci in ci_values:
        if ci.n is None:
            continue
        if not math.isclose(ci.confidence_level, 0.90, abs_tol=0.02):
            continue
        if not _has_required_design(ci.design_hint):
            continue
        return ci
    return None


def _has_required_design(design_hint: str | None) -> bool:
    if not design_hint:
        return False
    hint = design_hint.lower()
    return "2x2" in hint and "log" in hint


def _derive_from_ci(
    ci: CIValue,
    cv_confirmed: bool,
    use_fallback: bool,
) -> Tuple[CVInfo, List[OpenQuestion]]:
    warnings: List[str] = []
    open_questions: List[OpenQuestion] = []

    if ci.ci_low >= ci.ci_high or ci.ci_low <= 0 or ci.ci_high <= 0:
        warnings.append("invalid_ci_bounds")
        return (
            CVInfo(
                value=None,
                source="derived_from_ci",
                cv_source="derived_from_ci",
                confidence="low",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=ci.evidence,
                warnings=warnings,
            ),
            open_questions,
        )

    if ci.ci_low <= 0 or ci.ci_high >= 2:
        warnings.append("ci_outside_ratio_bounds")

    if ci.n is not None and ci.n < 6:
        warnings.append("small_n")

    health = powertost_health()
    if health.get("powertost_ok"):
        cv_val, runner_warnings = run_cvfromci(ci.ci_low, ci.ci_high, int(ci.n), "2x2")
        warnings.extend(runner_warnings)
        return (
            CVInfo(
                value=cv_val,
                source="derived_from_ci",
                cv_source="derived_from_ci",
                parameter=ci.param,
                confidence="medium" if cv_val is not None else "low",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=ci.evidence,
                warnings=warnings,
            ),
            open_questions,
        )

    if _fallback_allowed(use_fallback):
        cv_val = _approx_cv_from_ci(ci.ci_low, ci.ci_high, ci.n or 0, ci.confidence_level)
        warnings.append("approximation_for_testing_only")
        warnings.append("powertost_unavailable")
        return (
            CVInfo(
                value=cv_val,
                source="derived_from_ci",
                cv_source="derived_from_ci",
                parameter=ci.param,
                confidence="low",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=ci.evidence,
                warnings=warnings,
            ),
            open_questions,
        )

    open_questions.append(
        OpenQuestion(
            category="cv",
            question="Install R/PowerTOST or provide CV manually.",
            priority="high",
            linked_rule_id="CVFROMCI_POWERTOST",
        )
    )
    warnings.append("powertost_unavailable")
    return (
        CVInfo(
            value=None,
            source="derived_from_ci",
                cv_source="derived_from_ci",
                parameter=ci.param,
                confidence="low",
                requires_human_confirm=True,
                confirmed_by_user=bool(cv_confirmed),
                evidence=ci.evidence,
                warnings=warnings,
        ),
        open_questions,
    )


def _fallback_allowed(use_fallback: bool) -> bool:
    return use_fallback or os.getenv("ALLOW_CVFROMCI_FALLBACK", "").lower() == "true"


def _approx_cv_from_ci(ci_low: float, ci_high: float, n: int, confidence_level: float) -> float | None:
    if n <= 0:
        return None
    z = 1.645 if math.isclose(confidence_level, 0.90, abs_tol=0.01) else 1.96
    try:
        se = (math.log(ci_high) - math.log(ci_low)) / (2 * z)
        sigma = se * math.sqrt(n / 2.0)
        cv = math.sqrt(max(0.0, math.exp(sigma * sigma) - 1.0)) * 100.0
        return cv
    except Exception:
        return None

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import yaml

from backend.schemas import (
    CIValue,
    CVInfo,
    DataQuality,
    DataQualityComponents,
    PKValue,
    SourceCandidate,
    ValidationIssue,
)


def compute_data_quality(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    sources: List[SourceCandidate],
    cv_info: CVInfo,
    validation_issues: List[ValidationIssue],
    use_mock_extractor: bool = False,
    mock_path: str = "docs/mock_extractor_output.json",
    reg_rules_path: str = "backend/rules/reg_rules.yaml",
    criteria_path: str = "docs/DATA_QUALITY_CRITERIA.md",
) -> DataQuality:
    reasons: List[str] = []
    criteria = _load_criteria(criteria_path)

    eval_pk, eval_ci, mock_used = _maybe_load_mock(
        pk_values,
        ci_values,
        use_mock_extractor,
        mock_path,
    )
    if mock_used:
        reasons.append("Using mock extractor output for DQI.")

    completeness, completeness_reasons = _compute_completeness(
        eval_pk,
        eval_ci,
        cv_info,
        sources,
        reg_rules_path,
    )
    reasons.extend(completeness_reasons)

    traceability = _compute_traceability(eval_pk, eval_ci, reasons)
    plausibility = _compute_plausibility(validation_issues, reasons)
    consistency = _compute_consistency(eval_pk, reasons)
    source_quality = _compute_source_quality(sources, reasons)

    weights = criteria["weights"]
    score = _weighted_score(
        {
            "completeness": completeness,
            "traceability": traceability,
            "plausibility": plausibility,
            "consistency": consistency,
            "source_quality": source_quality,
        },
        weights,
    )
    level = _score_level(score, criteria["thresholds"])

    if criteria["todo"]:
        reasons.append("TODO: Data quality criteria not defined; using defaults.")

    cv_source = cv_info.cv_source or cv_info.source or "unknown"
    is_range = cv_source in ("range", "variability_range")
    allow_n_det = level in ("green", "yellow") and cv_info.confirmed_by_user and not is_range
    prefer_n_risk = level == "red" or is_range or not cv_info.confirmed_by_user

    return DataQuality(
        score=score,
        level=level,
        components=DataQualityComponents(
            completeness=round(completeness, 3),
            traceability=round(traceability, 3),
            plausibility=round(plausibility, 3),
            consistency=round(consistency, 3),
            source_quality=round(source_quality, 3),
        ),
        reasons=_dedupe_reasons(reasons),
        allow_n_det=allow_n_det,
        prefer_n_risk=prefer_n_risk,
    )


def _maybe_load_mock(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    use_mock_extractor: bool,
    mock_path: str,
) -> Tuple[List[PKValue], List[CIValue], bool]:
    if use_mock_extractor or _needs_mock(pk_values, ci_values):
        try:
            with open(mock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            mock_pk = [PKValue(**item) for item in data.get("pk_values", [])]
            mock_ci = [CIValue(**item) for item in data.get("ci_values", [])]
            return mock_pk, mock_ci, True
        except Exception:
            return pk_values, ci_values, False
    return pk_values, ci_values, False


def _needs_mock(pk_values: List[PKValue], ci_values: List[CIValue]) -> bool:
    if not pk_values and not ci_values:
        return True
    pk_has_evidence = any(pk.evidence for pk in pk_values)
    ci_has_evidence = any(ci.evidence for ci in ci_values)
    return not (pk_has_evidence or ci_has_evidence)


def _compute_completeness(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    cv_info: CVInfo,
    sources: List[SourceCandidate],
    reg_rules_path: str,
) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    required = _load_required_pk(reg_rules_path)
    present = {pk.name for pk in pk_values}

    base_required = [p for p in required if p not in ("t1/2", "lambda_z")]
    missing: List[str] = [p for p in base_required if p not in present]

    half_required = any(p in required for p in ("t1/2", "lambda_z"))
    if half_required and not (("t1/2" in present) or ("lambda_z" in present)):
        missing.append("t1/2 or lambda_z")

    required_total = len(base_required) + (1 if half_required else 0)
    satisfied = max(0, required_total - len(missing))
    pk_ratio = satisfied / required_total if required_total else 1.0

    if missing:
        reasons.append(f"Missing required PK parameters: {', '.join(missing)}.")

    cv_present = 1.0 if cv_info.value is not None else 0.0
    if cv_present == 0.0:
        reasons.append("CVintra not available.")

    ci_present = 1.0 if ci_values else 0.0
    if ci_present == 0.0:
        reasons.append("CI values not available.")

    n_present = 1.0 if any(ci.n is not None for ci in ci_values) else 0.0
    if n_present == 0.0:
        reasons.append("Sample size n not available near CI/GMR context.")

    conditions_present = 1.0 if any(s.feeding or s.species for s in sources) else (0.5 if sources else 0.0)
    if conditions_present == 0.0:
        reasons.append("Study conditions (fed/fasted, species) not available.")

    completeness = (pk_ratio + cv_present + ci_present + n_present + conditions_present) / 5.0
    return completeness, reasons


def _compute_traceability(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    reasons: List[str],
) -> float:
    numeric_items = [pk for pk in pk_values if pk.value is not None] + ci_values
    if not numeric_items:
        reasons.append("No numeric values for traceability scoring.")
        return 0.0
    with_evidence = sum(1 for item in numeric_items if item.evidence)
    traceability = with_evidence / len(numeric_items)
    if traceability < 1.0:
        reasons.append("Some numeric values lack evidence excerpts.")
    return traceability


def _compute_plausibility(validation_issues: List[ValidationIssue], reasons: List[str]) -> float:
    error_count = sum(1 for i in validation_issues if i.severity == "ERROR")
    warn_count = sum(1 for i in validation_issues if i.severity == "WARN")
    penalty = min(1.0, error_count * 0.3 + warn_count * 0.1)
    plausibility = max(0.0, 1.0 - penalty)
    if error_count > 0:
        reasons.append("Validation errors detected in PK values.")
    elif warn_count > 0:
        reasons.append("Validation warnings detected in PK values.")
    return plausibility


def _compute_consistency(pk_values: List[PKValue], reasons: List[str]) -> float:
    conflicts = sum(1 for pk in pk_values if pk.warnings and "conflict_detected" in pk.warnings)
    if conflicts == 0:
        return 1.0
    ratio = conflicts / max(1, len(pk_values))
    consistency = max(0.0, 1.0 - min(0.7, ratio))
    reasons.append("Conflicting values detected across sources.")
    return consistency


def _compute_source_quality(sources: List[SourceCandidate], reasons: List[str]) -> float:
    if not sources:
        reasons.append("Source metadata missing; source quality assumed moderate.")
        return 0.85

    human = any(s.species == "human" for s in sources)
    animal = any(s.species == "animal" for s in sources)
    if human:
        species_score = 1.0
    elif animal:
        species_score = 0.7
        reasons.append("Only animal sources detected.")
    else:
        species_score = 0.9

    tags = [tag for s in sources for tag in (s.type_tags or [])]
    if "BE" in tags or "PK" in tags:
        tag_score = 1.0
    elif "review" in tags:
        tag_score = 0.85
        reasons.append("Only review sources detected.")
    else:
        tag_score = 0.9

    return round(species_score * tag_score, 3)


def _load_required_pk(reg_rules_path: str) -> List[str]:
    try:
        with open(reg_rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        return ((rules.get("required_pk") or {}).get("decision_85") or {}).get("parameters") or []
    except Exception:
        return ["Cmax", "AUC0-t", "AUC0-inf", "t1/2", "lambda_z"]


def _load_criteria(criteria_path: str) -> Dict[str, object]:
    if not os.path.exists(criteria_path):
        return _default_criteria(todo=True)
    try:
        with open(criteria_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if not text or "TODO" in text.upper():
            return _default_criteria(todo=True)
    except Exception:
        return _default_criteria(todo=True)
    return _default_criteria(todo=False)


def _default_criteria(todo: bool) -> Dict[str, object]:
    return {
        "todo": todo,
        "weights": {
            "completeness": 0.25,
            "traceability": 0.25,
            "plausibility": 0.2,
            "consistency": 0.2,
            "source_quality": 0.1,
        },
        "thresholds": {
            "green": 80,
            "yellow": 55,
        },
    }


def _weighted_score(components: Dict[str, float], weights: Dict[str, float]) -> int:
    total_weight = sum(weights.values()) or 1.0
    score = 0.0
    for key, value in components.items():
        weight = weights.get(key, 0.0)
        score += value * weight
    return int(round((score / total_weight) * 100))


def _score_level(score: int, thresholds: Dict[str, int]) -> str:
    green = thresholds.get("green", 80)
    yellow = thresholds.get("yellow", 60)
    if score >= green:
        return "green"
    if score >= yellow:
        return "yellow"
    return "red"


def _dedupe_reasons(reasons: List[str], max_items: int = 5) -> List[str]:
    seen = set()
    out: List[str] = []
    for reason in reasons:
        if reason not in seen:
            out.append(reason)
            seen.add(reason)
        if len(out) >= max_items:
            break
    return out

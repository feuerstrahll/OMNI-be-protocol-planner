from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

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
    use_fallback: bool = False,
    mock_path: str = "docs/mock_extractor_output.json",
    reg_rules_path: str = "backend/rules/reg_rules.yaml",
    criteria_path: str = "docs/DATA_QUALITY_CRITERIA.md",
    *,
    pk_warnings: Optional[List[str]] = None,
    protocol_condition: Optional[str] = None,
    selected_sources: Optional[List[str]] = None,
    calc_notes: Optional[List[str]] = None,
) -> DataQuality:
    reasons: List[str] = []
    criteria = _load_criteria(criteria_path)
    hard_gates: List[str] = []

    eval_pk, eval_ci, mock_used = _maybe_load_mock(
        pk_values,
        ci_values,
        use_mock_extractor,
        use_fallback,
        mock_path,
    )
    if mock_used:
        reasons.append("Using mock extractor output for DQI.")

    # Hard gate: fallback_pk in pk_warnings (incl. when mock used for DQI)
    all_warnings = list(pk_warnings or []) + (["fallback_pk"] if mock_used else [])
    if "fallback_pk" in all_warnings:
        hard_gates.append("fallback_pk")
        reasons.insert(0, "Hard gate: PK/CV from fallback — export of N_det blocked.")

    # Hard gate: protocol_condition conflicts with evidence (fed/fasted mismatch)
    if protocol_condition and protocol_condition in ("fed", "fasted") and "condition_tagging_missing" in (calc_notes or []):
        hard_gates.append("protocol_condition_conflicts_with_evidence")
        reasons.insert(0, "Hard gate: Protocol condition (fed/fasted) conflicts with untagged evidence — resolve before final export.")

    # Hard gate: selected_sources set but none match actual sources used
    if selected_sources:
        sources_ref_ids = {getattr(s, "ref_id", "") for s in sources if getattr(s, "ref_id", None)}
        overlap = set(selected_sources) & sources_ref_ids
        if not overlap and len(selected_sources) > 0:
            hard_gates.append("selected_sources_mismatch")
            reasons.insert(0, "Hard gate: Selected sources do not match any source in the report — verify sources.")

    completeness, completeness_reasons = _compute_completeness(
        eval_pk,
        eval_ci,
        cv_info,
        sources,
        reg_rules_path,
    )
    reasons.extend(completeness_reasons)

    traceability = _compute_traceability(eval_pk, eval_ci, reasons, cv_info)
    plausibility = _compute_plausibility(validation_issues, reasons)
    consistency = _compute_consistency(eval_pk, reasons)
    source_quality = _compute_source_quality(sources, reasons)

    # --- warning-based penalties ---
    warning_codes = _collect_warnings_for_dqi(pk_values, ci_values, validation_issues)
    penalties_cfg = criteria.get("penalties") or {}
    components = {
        "completeness": completeness,
        "traceability": traceability,
        "plausibility": plausibility,
        "consistency": consistency,
        "source_quality": source_quality,
    }
    for code, pen in penalties_cfg.items():
        if code in warning_codes:
            comp = pen.get("component")
            val = float(pen.get("value", 0))
            if comp in components:
                components[comp] = max(0.0, components[comp] - val)
                reasons.append(f"Penalty on {comp}: warning '{code}'.")
    completeness = components["completeness"]
    traceability = components["traceability"]
    plausibility = components["plausibility"]
    consistency = components["consistency"]
    source_quality = components["source_quality"]

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

    hard_red = _missing_primary_endpoints(eval_pk)
    hard_red_codes = set(criteria.get("hard_red_codes") or [])
    if (
        not hard_red
        and "traceability_zero" in hard_red_codes
        and traceability == 0.0
    ):
        hard_red = True
        reasons.insert(0, "Hard Red Flag: No evidence for any numeric value (traceability=0).")
    if hard_red:
        reasons.insert(0, "Hard Red Flag: Missing primary PK endpoints (AUC and Cmax).")
        score = 0
        level = "red"

    cv_source = cv_info.cv_source or cv_info.source or "unknown"
    is_range = cv_source in ("range", "variability_range")
    # Trust policy: allow N_det if user confirmed, or if confidence_score >= threshold and not doubtful
    from backend.services.cv_trust import AUTO_CV_THRESHOLD, is_cv_doubtful

    _cv_score = cv_info.confidence_score if cv_info.confidence_score is not None else 0.0
    _cv_eligible_auto = _cv_score >= AUTO_CV_THRESHOLD and not is_cv_doubtful(cv_info)
    allow_n_det = (
        level in ("green", "yellow")
        and (cv_info.confirmed_by_user or _cv_eligible_auto)
        and not is_range
    )
    prefer_n_risk = level == "red" or is_range or not (cv_info.confirmed_by_user or _cv_eligible_auto)
    if hard_red:
        allow_n_det = False
        prefer_n_risk = True

    # Apply hard gates (override level and block allow_n_det)
    if hard_gates:
        if "fallback_pk" in hard_gates:
            if level == "green":
                level = "yellow"
                score = min(score, 79)
            allow_n_det = False
        if "protocol_condition_conflicts_with_evidence" in hard_gates:
            if level == "green":
                level = "yellow"
                score = min(score, 79)
            allow_n_det = False
        if "selected_sources_mismatch" in hard_gates:
            level = "red"
            score = 0
            allow_n_det = False
            prefer_n_risk = True

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
    use_fallback: bool,
    mock_path: str,
) -> Tuple[List[PKValue], List[CIValue], bool]:
    # When use_fallback=False, never auto-inject mock; only use mock when explicitly requested.
    if use_mock_extractor or (use_fallback and _needs_mock(pk_values, ci_values)):
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


def _is_traceable_source(evidence_list: list) -> bool:
    """Evidence is traceable if it has PMID/PMCID/URL; manual://, assumption, fallback:// = not traceable."""
    if not evidence_list:
        return False
    for ev in evidence_list:
        if ev is None:
            continue
        src = (
            (ev.get("source") if isinstance(ev, dict) else getattr(ev, "source", None))
            or (ev.get("source_id") if isinstance(ev, dict) else getattr(ev, "source_id", None))
            or (ev.get("pmid_or_url") if isinstance(ev, dict) else getattr(ev, "pmid_or_url", None))
            or ""
        )
        src = str(src).lower()
        if src.startswith("pmid:") or src.startswith("pmcid:") or (src.startswith("http") and "fallback" not in src):
            return True
        if (ev.get("pmid") if isinstance(ev, dict) else getattr(ev, "pmid", None)):
            return True
        if (ev.get("url") if isinstance(ev, dict) else getattr(ev, "url", None)):
            return True
    return False


def _compute_traceability(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    reasons: List[str],
    cv_info: Optional[CVInfo] = None,
) -> float:
    numeric_items = [pk for pk in pk_values if pk.value is not None] + list(ci_values)
    items_to_score: list = list(numeric_items)
    if cv_info and cv_info.value is not None:
        items_to_score.append(cv_info)
    if not items_to_score:
        reasons.append("No numeric values for traceability scoring.")
        return 0.0
    with_traceable = sum(
        1 for item in items_to_score
        if _is_traceable_source(getattr(item, "evidence", None) or [])
    )
    traceability = with_traceable / len(items_to_score)
    if traceability < 1.0:
        reasons.append("Some numeric values lack traceable evidence (PMID/URL); assumptions reduce DQI.")
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


def _missing_primary_endpoints(pk_values: List[PKValue]) -> bool:
    has_cmax = False
    has_auc = False
    for pk in pk_values:
        if pk.value is None:
            continue
        name = (pk.name or "").upper()
        if name == "CMAX":
            has_cmax = True
        if name.startswith("AUC"):
            has_auc = True
    return not has_cmax and not has_auc


def _load_required_pk(reg_rules_path: str) -> List[str]:
    try:
        with open(reg_rules_path, "r", encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        return ((rules.get("required_pk") or {}).get("decision_85") or {}).get("parameters") or []
    except Exception:
        return ["Cmax", "AUC0-t", "AUC0-inf", "t1/2", "lambda_z"]


def _load_criteria(criteria_path: str) -> Dict[str, object]:
    fixed_yaml = os.path.join("docs", "DATA_QUALITY_CRITERIA.yaml")
    paths = [fixed_yaml, criteria_path]
    for path in paths:
        if os.path.exists(path) and path.endswith(".yaml"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if data.get("weights") and data.get("thresholds"):
                    data.setdefault("todo", False)
                    data.setdefault("penalties", {})
                    data.setdefault("hard_red_codes", [])
                    return data
            except Exception:
                pass
    return _default_criteria(todo=True)


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


def _collect_warnings_for_dqi(
    pk_values: List[PKValue],
    ci_values: List[CIValue],
    validation_issues: List[ValidationIssue],
) -> set:
    codes: set = set()
    for pk in pk_values:
        for w in (pk.warnings or []):
            if w == "unit_not_allowed":
                codes.add("unit_suspect")
            if w == "unit_normalization_failed":
                codes.add("suspicious_conversion")
            if "conflict_detected" in w:
                codes.add("conflicting_values")
    for issue in validation_issues:
        if "conflict" in issue.message.lower():
            codes.add("conflicting_values")
    numeric_items = [pk for pk in pk_values if pk.value is not None] + list(ci_values)
    if numeric_items:
        if not any(getattr(i, "evidence", None) for i in numeric_items):
            codes.add("missing_evidence")
        if not any(
            any(
                getattr(ev, "pmid_or_url", None)
                or getattr(ev, "pmid", None)
                or getattr(ev, "url", None)
                or getattr(ev, "source_id", None)
                for ev in (getattr(i, "evidence", None) or [])
                if ev is not None
            )
            for i in numeric_items
        ):
            codes.add("missing_source")
    return codes

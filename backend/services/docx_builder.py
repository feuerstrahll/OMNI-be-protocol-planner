from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from backend.services.docx.synopsis_builder import AUTO_FILLED_HEADINGS, build_synopsis_sections
from backend.services.docx.writer import ensure_dqi_summary, write_synopsis_single_table_docx
from backend.services.render_utils import (
    DEFAULT_PLACEHOLDER,
    safe_join,
    safe_list,
    safe_num,
    safe_pct,
    safe_str,
    safe_table,
)
from backend.services.synopsis_requirements import REQUIRED_HEADINGS, evaluate_synopsis_completeness
from backend.services.utils import now_iso


class DocxRenderError(RuntimeError):
    def __init__(self, message: str, warnings: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.warnings = warnings or []


def safe(value: Any, default: str = DEFAULT_PLACEHOLDER) -> str:
    return safe_str(value, default=default)


def build_docx(all_json: Dict) -> str:
    inn = str(all_json.get("inn") or all_json.get("search", {}).get("inn") or "unknown")
    safe_inn = re.sub(r"[^a-zA-Z0-9_-]+", "_", inn).strip("_") or "unknown"
    protocol_id = all_json.get("protocol_id") or all_json.get("protocol", {}).get("id")
    protocol_status = all_json.get("protocol_status") or all_json.get("protocol", {}).get("status")
    if not protocol_id:
        date_str = now_iso().split("T")[0].replace("-", "")
        protocol_id = f"BE-{inn}-{date_str}"
        protocol_status = protocol_status or "Draft"
    elif not protocol_status:
        protocol_status = "Final"

    report = all_json or {}
    sources = _as_list(report.get("sources"))
    pk_values = _as_list(report.get("pk_values"))
    ci_values = _as_list(report.get("ci_values"))
    reg_checks = _as_list(report.get("reg_check") or (report.get("reg_check_summary") or {}).get("items"))
    open_questions = _as_list(
        report.get("open_questions") or (report.get("reg_check_summary") or {}).get("open_questions")
    )

    sources_table = _build_sources_table(sources)
    pk_table = _build_pk_table(pk_values)
    ci_table = _build_ci_table(ci_values)
    reg_check_table = _build_reg_check_table(reg_checks)
    open_questions_list = list(open_questions)
    for item in reg_checks:
        if safe_str(_get(item, "status")).upper() == "CLARIFY":
            clarifications = _get(item, "what_to_clarify") or []
            if clarifications:
                for line in clarifications:
                    open_questions_list.append(
                        {
                            "question": safe_str(line),
                            "priority": "medium",
                            "category": "reg_check",
                            "linked_rule_id": safe_str(_get(item, "rule_id")),
                        }
                    )
            else:
                open_questions_list.append(
                    {
                        "question": safe_str(_get(item, "message")),
                        "priority": "medium",
                        "category": "reg_check",
                        "linked_rule_id": safe_str(_get(item, "rule_id")),
                    }
                )
    validation_issues = _as_list(report.get("validation_issues"))
    for issue in validation_issues:
        message = safe_str(_get(issue, "message"))
        if message:
            open_questions_list.append(
                {
                    "question": f"Validation: {message}",
                    "priority": "medium",
                    "category": "validation",
                    "linked_rule_id": safe_str(_get(issue, "metric")),
                }
            )
    open_questions_table = _build_open_questions_table(open_questions_list)

    synopsis_eval = evaluate_synopsis_completeness(report)
    if synopsis_eval.get("missing_fields"):
        for heading in synopsis_eval.get("missing_fields", []):
            if heading in AUTO_FILLED_HEADINGS:
                continue
            open_questions_table.append(
                {
                    "question": f"Provide content for section: {heading}",
                    "priority": "medium",
                    "category": "synopsis",
                    "linked_rule_id": "SYNOPSIS_MISSING",
                }
            )

    data_quality = report.get("dqi") or report.get("data_quality") or {}
    dq_score = safe_num(_get(data_quality, "score"), default=DEFAULT_PLACEHOLDER, ndigits=0)
    dq_level = safe_str(_get(data_quality, "level"), default="Not computed")
    dq_reasons_list = _get(data_quality, "reasons") or []
    dq_reasons = safe_join(dq_reasons_list, default=DEFAULT_PLACEHOLDER)
    dq_reasons_top = safe_join(dq_reasons_list[:3], default=DEFAULT_PLACEHOLDER)
    dq_summary = f"{dq_level} (score: {dq_score})"
    if dq_level == "red" and dq_reasons_list:
        if len(open_questions_table) == 1 and open_questions_table[0].get("question") == "No items":
            open_questions_table = []
        for reason in dq_reasons_list[:3]:
            open_questions_table.append(
                {
                    "question": f"DQI: {safe_str(reason)}",
                    "priority": "high",
                    "category": "data_quality",
                    "linked_rule_id": "DQI",
                }
            )

    cv_info = report.get("cv") or report.get("cv_info") or {}
    cv_value_raw = _get(cv_info, "value")
    cv_range_low = _get(cv_info, "range_low")
    cv_range_high = _get(cv_info, "range_high")
    cv_source = safe_str(
        _get(cv_info, "method") or _get(cv_info, "cv_source") or _get(cv_info, "source"),
        default=DEFAULT_PLACEHOLDER,
    )
    cv_confirmed = bool(_get(cv_info, "confirmed_by_human") or _get(cv_info, "confirmed_by_user")) if cv_info else False
    cv_conf_label = safe_str(_get(cv_info, "confidence"), default=DEFAULT_PLACEHOLDER)
    cv_conf_score = _get(cv_info, "confidence_score")

    design = report.get("design") or (report.get("study") or {}).get("design") or {}
    design_recommendation = safe_str(
        _get(design, "recommendation") or _get(design, "recommended"),
        default="Design not determined (insufficient inputs)",
    )
    design_reasoning = safe_str(_get(design, "reasoning_text"), default=DEFAULT_PLACEHOLDER)

    sample_det = report.get("sample_size_det") or _get(report.get("sample_size") or {}, "n_det")
    allow_n_det = _get(data_quality, "allow_n_det")
    dq_reasons_list = _get(data_quality, "reasons") or []
    n_det_total = safe_num(_get(sample_det, "n_total") or _get(sample_det, "n_analysis"))
    n_det_rand = safe_num(_get(sample_det, "n_rand"))
    n_det_screen = safe_num(_get(sample_det, "n_screen"))
    n_det_power = safe_pct(_get(sample_det, "power"))
    n_det_alpha = safe_pct(_get(sample_det, "alpha"))
    n_det_dropout = safe_pct(_get(sample_det, "dropout"))
    n_det_screen_fail = safe_pct(_get(sample_det, "screen_fail"))
    n_det_reason_parts: List[str] = []
    if not sample_det:
        if cv_value_raw is None and cv_range_low is None and cv_range_high is None:
            n_det_reason_parts.append("CV unavailable")
        elif (_get(cv_info, "cv_source") or _get(cv_info, "source")) in ("range", "variability_range") or (
            cv_range_low is not None and cv_range_high is not None
        ):
            n_det_reason_parts.append("CV not eligible for N_det (range provided; risk mode)")
        elif allow_n_det is False:
            if any("Missing primary PK endpoints" in str(r) for r in dq_reasons_list):
                n_det_reason_parts.append("required PK endpoints missing (Cmax/AUC)")
            else:
                n_det_reason_parts.append("N_det blocked by data quality (allow_n_det=False)")
        elif _get(cv_info, "requires_human_confirm") and not cv_confirmed:
            n_det_reason_parts.append("CV not eligible for N_det (requires confirmation / low confidence)")
        else:
            n_det_reason_parts.append("N_det not computed (insufficient inputs)")

    sample_risk = report.get("sample_size_risk") or _get(report.get("sample_size") or {}, "n_risk")
    n_risk_status = "N_risk not computed (requires CV range/distribution)"
    n_risk_targets = DEFAULT_PLACEHOLDER
    n_risk_p_success = DEFAULT_PLACEHOLDER
    n_risk_notes = DEFAULT_PLACEHOLDER
    if sample_risk:
        n_risk_status = ""
        n_risk_targets = safe_join(_format_dict(_get(sample_risk, "n_targets")), default=DEFAULT_PLACEHOLDER)
        n_risk_p_success = safe_join(
            _format_dict(_get(sample_risk, "p_success_at_n"), ndigits=2),
            default=DEFAULT_PLACEHOLDER,
        )
        n_risk_notes = safe_join(_get(sample_risk, "sensitivity_notes") or [], default=DEFAULT_PLACEHOLDER)

    replacement_subjects = _yes_no(report.get("replacement_subjects"))
    visit_day_numbering = safe_str(report.get("visit_day_numbering"), default="continuous across periods")
    protocol_condition = safe_str(report.get("protocol_condition") or (report.get("study") or {}).get("protocol_condition"))
    if not protocol_condition:
        protocol_condition = DEFAULT_PLACEHOLDER

    calc_alpha = n_det_alpha if sample_det else safe_pct(_get(report, "alpha"))
    calc_power = n_det_power if sample_det else safe_pct(_get(report, "power"))
    calc_dropout = n_det_dropout if sample_det else safe_pct(_get(report, "dropout"))
    calc_screen_fail = n_det_screen_fail if sample_det else safe_pct(_get(report, "screen_fail"))

    sample_lines: List[str] = []
    sample_lines.append(f"Design: {design_recommendation}")
    sample_lines.append(
        f"Calc params: alpha={calc_alpha}, power={calc_power}, dropout={calc_dropout}, screen-fail={calc_screen_fail}"
    )
    cv_line_parts: List[str] = []
    if cv_range_low is not None or cv_range_high is not None:
        cv_line_parts.append(f"CV range: {safe_pct(cv_range_low)}-{safe_pct(cv_range_high)}")
    elif cv_value_raw is not None:
        cv_line_parts.append(f"CV: {safe_pct(cv_value_raw)}")
    else:
        cv_line_parts.append("CV: unavailable")
    if cv_source != DEFAULT_PLACEHOLDER:
        cv_line_parts.append(f"source={cv_source}")
    cv_flags: List[str] = []
    cv_flags.append("confirmed_by_user" if cv_confirmed else "not confirmed")
    eligible_flag = (
        allow_n_det
        and cv_value_raw is not None
        and cv_range_low is None
        and cv_range_high is None
    )
    cv_flags.append(f"eligible_for_n_det={'yes' if eligible_flag else 'no'}")
    if cv_conf_label != DEFAULT_PLACEHOLDER:
        cv_flags.append(f"confidence={cv_conf_label}")
    if cv_conf_score is not None:
        try:
            cv_flags.append(f"confidence_score={float(cv_conf_score):.2f}")
        except Exception:
            cv_flags.append(f"confidence_score={cv_conf_score}")
    cv_line_parts.append("; ".join(cv_flags))
    sample_lines.append("CV info: " + " | ".join(cv_line_parts))

    if sample_det:
        sample_lines.append(
            f"N_det: total={n_det_total}; rand={n_det_rand}; screen={n_det_screen}"
        )
        det_warn = safe_join(_get(sample_det, "warnings") or [], default="")
        if det_warn:
            sample_lines.append(f"N_det notes: {det_warn}")
    else:
        reason_line = "; ".join(n_det_reason_parts) if n_det_reason_parts else "N_det not computed"
        sample_lines.append(f"N_det not computed: {reason_line}")

    if sample_risk:
        risk_line = f"N_risk targets: {n_risk_targets}"
        if n_risk_p_success != DEFAULT_PLACEHOLDER:
            risk_line = f"{risk_line}; p_success: {n_risk_p_success}"
        if n_risk_notes != DEFAULT_PLACEHOLDER:
            risk_line = f"{risk_line}; notes: {n_risk_notes}"
        sample_lines.append(risk_line)
    elif n_risk_status:
        sample_lines.append(n_risk_status)

    sample_size_line = "\n".join([line for line in sample_lines if line])

    synopsis_sections = build_synopsis_sections(report, dq_summary, open_questions_table, sample_size_line)

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"synopsis_{safe_inn}.docx")
    try:
        write_synopsis_single_table_docx(out_path, synopsis_sections, sources)
    except Exception as exc:
        warning = f"DOCX_BUILD_FAILED: {type(exc).__name__}: {exc}"
        raise DocxRenderError(warning, warnings=[warning]) from exc

    # Ensure DQI block is explicitly present as paragraphs (not only table)
    ensure_dqi_summary(out_path, dq_summary, dq_reasons_top)
    return out_path


def _as_list(value: Any) -> List[dict]:
    if value is None:
        return []
    return list(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _yes_no(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return DEFAULT_PLACEHOLDER


def _build_sources_table(sources: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for src in sources:
        rows.append(
            {
                "pmid": safe_str(_get(src, "pmid")),
                "title": safe_str(_get(src, "title")),
                "year": safe_num(_get(src, "year")),
                "type_tags": safe_join(_get(src, "type_tags") or []),
                "species": safe_str(_get(src, "species")),
                "feeding": safe_str(_get(src, "feeding")),
                "url": safe_str(_get(src, "url")),
            }
        )
    if not rows:
        rows = [{"pmid": DEFAULT_PLACEHOLDER, "title": "No sources selected / Found"}]
    return safe_table(rows, default=[{"pmid": DEFAULT_PLACEHOLDER, "title": "No sources selected / Found"}])


def _build_pk_table(pk_values: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for pk in pk_values:
        evidence_text = _evidence_text(_get(pk, "evidence"))
        rows.append(
            {
                "name": safe_str(_get(pk, "name")),
                "value": safe_num(_get(pk, "value")),
                "unit": safe_str(_get(pk, "unit")),
                "evidence": safe_str(evidence_text, default="evidence not available"),
                "warnings": safe_join(_get(pk, "warnings") or [], default=DEFAULT_PLACEHOLDER),
            }
        )
    if not rows:
        rows = [{"name": "No PK values extracted", "value": DEFAULT_PLACEHOLDER, "unit": DEFAULT_PLACEHOLDER}]
    return safe_table(rows, default=[{"name": "No PK values extracted", "value": DEFAULT_PLACEHOLDER}])


def _build_ci_table(ci_values: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for ci in ci_values:
        evidence_text = _evidence_text(_get(ci, "evidence"))
        rows.append(
            {
                "param": safe_str(_get(ci, "param")),
                "ci_low": safe_num(_get(ci, "ci_low")),
                "ci_high": safe_num(_get(ci, "ci_high")),
                "confidence": safe_pct(_get(ci, "confidence_level")),
                "gmr": safe_num(_get(ci, "gmr")),
                "n": safe_num(_get(ci, "n")),
                "design_hint": safe_str(_get(ci, "design_hint")),
                "evidence": safe_str(evidence_text, default="evidence not available"),
            }
        )
    if not rows:
        rows = [{"param": "No CI values extracted", "ci_low": DEFAULT_PLACEHOLDER}]
    return safe_table(rows, default=[{"param": "No CI values extracted"}])


def _build_reg_check_table(checks: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for item in checks:
        rows.append(
            {
                "status": safe_str(_get(item, "status")),
                "message": safe_str(_get(item, "message")),
                "what_to_clarify": safe_join(_get(item, "what_to_clarify") or [], default=DEFAULT_PLACEHOLDER),
                "rule_id": safe_str(_get(item, "rule_id")),
            }
        )
    if not rows:
        rows = [{"status": DEFAULT_PLACEHOLDER, "message": "No items"}]
    return safe_table(rows, default=[{"message": "No items"}])


def _build_open_questions_table(open_questions: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for item in open_questions:
        rows.append(
            {
                "question": safe_str(_get(item, "question")),
                "priority": safe_str(_get(item, "priority")),
                "category": safe_str(_get(item, "category")),
                "linked_rule_id": safe_str(_get(item, "linked_rule_id")),
            }
        )
    if not rows:
        rows = [{"question": "No items", "priority": DEFAULT_PLACEHOLDER}]
    return safe_table(rows, default=[{"question": "No items"}])


def _evidence_text(evidence_list: Any) -> str:
    evidence = _as_list(evidence_list)
    if not evidence:
        return "evidence not available"
    ev = evidence[0]
    excerpt = _get(ev, "excerpt") or _get(ev, "snippet")
    if excerpt:
        return safe_str(excerpt)
    source = _get(ev, "pmid_or_url") or _get(ev, "pmid") or _get(ev, "url") or _get(ev, "source")
    if source:
        return f"evidence: {safe_str(source)}"
    return "evidence not available"


def _build_safe_data_map(**items: Any) -> Dict[str, Any]:
    return {key: safe_str(value) for key, value in items.items()}


def _find_ci_fields(ci_values: List[dict]) -> tuple[str, str, str]:
    for ci in ci_values:
        level = _get(ci, "confidence_level")
        if level is None or abs(float(level) - 0.90) <= 0.02:
            return (
                safe_num(_get(ci, "ci_low")),
                safe_num(_get(ci, "ci_high")),
                safe_num(_get(ci, "n")),
            )
    return DEFAULT_PLACEHOLDER, DEFAULT_PLACEHOLDER, DEFAULT_PLACEHOLDER


def _format_dict(data: Any, ndigits: int | None = None) -> List[str]:
    if not data:
        return []
    if isinstance(data, dict):
        items = data.items()
    else:
        items = list(data)
    formatted = []
    for key, value in items:
        formatted.append(f"{key}: {safe_num(value, ndigits=ndigits)}")
    return formatted


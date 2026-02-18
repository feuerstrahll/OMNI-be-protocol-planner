from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import jinja2
from docxtpl import DocxTemplate

from backend.services.render_utils import (
    DEFAULT_PLACEHOLDER,
    safe_join,
    safe_num,
    safe_pct,
    safe_str,
    safe_table,
)
from backend.services.utils import now_iso


class DocxRenderError(RuntimeError):
    def __init__(self, message: str, warnings: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.warnings = warnings or []


def build_docx(all_json: Dict) -> str:
    template_path = os.path.join("templates", "synopsis_template.docx")
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    doc = DocxTemplate(template_path)
    if getattr(doc, "jinja_env", None) is not None:
        doc.jinja_env.undefined = _SafeUndefined
    else:
        doc.jinja_env = jinja2.Environment(undefined=_SafeUndefined)

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
    reg_checks = _as_list(report.get("reg_check"))
    open_questions = _as_list(report.get("open_questions") or (report.get("reg") or {}).get("open_questions"))

    sources_table = _build_sources_table(sources)
    pk_table = _build_pk_table(pk_values)
    ci_table = _build_ci_table(ci_values)
    reg_check_table = _build_reg_check_table(reg_checks)
    open_questions_table = _build_open_questions_table(open_questions)

    data_quality = report.get("data_quality") or {}
    dq_score = safe_num(_get(data_quality, "score"), default=DEFAULT_PLACEHOLDER, ndigits=0)
    dq_level = safe_str(_get(data_quality, "level"), default="Not computed")
    dq_reasons = safe_join(_get(data_quality, "reasons") or [], default=DEFAULT_PLACEHOLDER)

    cv_info = report.get("cv_info") or {}
    cv_value = safe_pct(_get(cv_info, "value"))
    cv_source = safe_str(_get(cv_info, "cv_source") or _get(cv_info, "source"), default=DEFAULT_PLACEHOLDER)
    cv_confirmed = bool(_get(cv_info, "confirmed_by_user")) if cv_info else False
    cv_status_line = "CV: not available"
    if _get(cv_info, "value") is not None:
        cv_status_line = f"CV: {cv_value}"
    if not cv_info:
        cv_status_line = "CV: not available"
    cv_confirmation_line = "CV not confirmed (N_det disabled)" if cv_info and not cv_confirmed else ""
    cv_ci_low, cv_ci_high, cv_ci_n = _find_ci_fields(ci_values)

    design = report.get("design") or {}
    design_recommendation = safe_str(
        _get(design, "recommendation"), default="Design not determined (insufficient inputs)"
    )
    design_reasoning = safe_str(_get(design, "reasoning_text"), default=DEFAULT_PLACEHOLDER)

    sample_det = report.get("sample_size_det")
    n_det_status = "N_det not computed (requires confirmed CV)"
    n_det_total = DEFAULT_PLACEHOLDER
    n_det_rand = DEFAULT_PLACEHOLDER
    n_det_screen = DEFAULT_PLACEHOLDER
    if sample_det:
        n_det_status = ""
        n_det_total = safe_num(_get(sample_det, "n_total"))
        n_det_rand = safe_num(_get(sample_det, "n_rand"))
        n_det_screen = safe_num(_get(sample_det, "n_screen"))

    sample_risk = report.get("sample_size_risk")
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

    context = {
        "generated_at": safe_str(now_iso()),
        "inn": safe_str(inn),
        "protocol_id": safe_str(protocol_id),
        "protocol_status": safe_str(protocol_status),
        "replacement_subjects": replacement_subjects,
        "visit_day_numbering": visit_day_numbering,
        "sources": sources_table,
        "pk_values": pk_table,
        "ci_values": ci_table,
        "data_quality_score": dq_score,
        "data_quality_level": dq_level,
        "data_quality_reasons": dq_reasons,
        "cv_value": cv_value,
        "cv_source": cv_source,
        "cv_status_line": cv_status_line,
        "cv_confirmation_line": cv_confirmation_line or DEFAULT_PLACEHOLDER,
        "cv_ci_low": cv_ci_low,
        "cv_ci_high": cv_ci_high,
        "cv_ci_n": cv_ci_n,
        "design_recommendation": design_recommendation,
        "design_reasoning": design_reasoning,
        "n_det_status": n_det_status or DEFAULT_PLACEHOLDER,
        "n_det_total": n_det_total,
        "n_det_rand": n_det_rand,
        "n_det_screen": n_det_screen,
        "n_risk_status": n_risk_status or DEFAULT_PLACEHOLDER,
        "n_risk_targets": n_risk_targets,
        "n_risk_p_success": n_risk_p_success,
        "n_risk_notes": n_risk_notes,
        "reg_check": reg_check_table,
        "open_questions": open_questions_table,
        "open_questions_text": safe_join([q.get("question") for q in open_questions_table], default="No items"),
        "sources_note": "No sources selected / Found" if not sources else "",
        "pk_note": "No PK values extracted" if not pk_values else "",
        "ci_note": "No CI values extracted" if not ci_values else "",
        "data": _build_safe_data_map(
            inn=inn,
            protocol_id=protocol_id,
            protocol_status=protocol_status,
            replacement_subjects=replacement_subjects,
            visit_day_numbering=visit_day_numbering,
        ),
    }

    try:
        doc.render(context)
    except Exception as exc:
        warning = f"DOCX_RENDER_FAILED: {type(exc).__name__}: {exc}"
        raise DocxRenderError(warning, warnings=[warning]) from exc

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"synopsis_{safe_inn}.docx")
    doc.save(out_path)
    return out_path


class _SafeUndefined(jinja2.ChainableUndefined):
    def __str__(self) -> str:  # pragma: no cover - defensive
        return DEFAULT_PLACEHOLDER

    __repr__ = __str__


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

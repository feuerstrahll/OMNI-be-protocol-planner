from __future__ import annotations

from docx import Document

from backend.services.docx_builder import build_docx
from backend.services.synopsis_requirements import evaluate_synopsis_completeness


def test_synopsis_completeness_nonblocking():
    report = {"inn": "test"}
    result = evaluate_synopsis_completeness(report)
    assert isinstance(result, dict)
    assert "level" in result
    assert "notes" in result


def test_docx_includes_data_quality_summary_block():
    report = {
        "inn": "test",
        "data_quality": {
            "score": 0,
            "level": "red",
            "reasons": ["Hard Red Flag: Missing primary PK endpoints (AUC and Cmax)."],
        },
        "cv_info": {"value": None, "confirmed_by_user": False, "cv_source": "unknown"},
        "pk_values": [],
        "ci_values": [],
        "reg_check": [],
        "open_questions": [],
    }
    path = build_docx(report)
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Data Quality summary:" in text

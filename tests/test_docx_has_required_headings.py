from __future__ import annotations

from docx import Document

from backend.services.docx_builder import build_docx
from backend.services.synopsis_requirements import REQUIRED_HEADINGS


def test_docx_has_required_headings():
    report = {
        "inn": "test",
        "data_quality": {"score": 0, "level": "red", "reasons": []},
        "cv_info": {"value": None, "confirmed_by_user": False, "cv_source": "unknown"},
        "pk_values": [],
        "ci_values": [],
        "reg_check": [],
        "open_questions": [],
    }
    path = build_docx(report)
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    for heading in REQUIRED_HEADINGS:
        assert heading in text

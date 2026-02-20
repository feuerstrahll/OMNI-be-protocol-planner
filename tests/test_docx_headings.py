from __future__ import annotations

import os
import zipfile
from xml.etree import ElementTree

from backend.services.docx_builder import build_docx
from backend.services.synopsis_requirements import REQUIRED_HEADINGS


def _docx_text(docx_path: str) -> str:
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts = [
        node.text
        for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        if node.text
    ]
    return " ".join(texts)


def test_docx_contains_required_headings():
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
    assert os.path.exists(path)
    text = _docx_text(path)
    for heading in REQUIRED_HEADINGS:
        assert heading in text


def test_docx_contains_dqi_hard_red_flag():
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
    text = _docx_text(path)
    assert "Hard Red Flag: Missing primary PK endpoints (AUC and Cmax)." in text

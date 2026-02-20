"""
Final QA Smoke Test – 5 checkpoints
Run from project root: python _qa_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from xml.etree import ElementTree

# ── helpers ──────────────────────────────────────────────────────────
def docx_text(path: str) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    return " ".join(
        node.text for node in root.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
        ) if node.text
    )


def sep(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ── imports (must come after path setup) ─────────────────────────────
from backend.schemas import (
    CVInput, Evidence, NumericValue, PKExtractionResponse, PKValue, CIValue,
    CVInfo, SourceCandidate, ValidationIssue,
)
from backend.services.design_engine import DesignEngine
from backend.services.data_quality import compute_data_quality
from backend.services.docx_builder import build_docx
from backend.services.synopsis_requirements import REQUIRED_HEADINGS

engine = DesignEngine("backend/rules/design_rules.yaml")
ev = Evidence(source_type="URL", source="manual://test", snippet="test data")

ok_count = 0
fail_count = 0

def check(label: str, condition: bool, detail: str = ""):
    global ok_count, fail_count
    if condition:
        ok_count += 1
        print(f"  ✅ {label} {detail}")
    else:
        fail_count += 1
        print(f"  ❌ {label} {detail}")


# ================================================================
# QA-1  Happy Path: AUC, Cmax, CVintra=20%, T1/2=10h
# ================================================================
sep("QA-1: End-to-End Happy Path")
pk_values_happy = [
    PKValue(name="AUC0-t", value=500.0, unit="ng*h/mL", evidence=[ev]),
    PKValue(name="Cmax", value=40.0, unit="ng/mL", evidence=[ev]),
    PKValue(name="CVintra", value=20.0, unit="%", evidence=[ev]),
    PKValue(name="t1/2", value=10.0, unit="h", evidence=[ev]),
]
ci_happy = [
    CIValue(param="AUC", ci_low=0.90, ci_high=1.10, evidence=[ev]),
    CIValue(param="Cmax", ci_low=0.88, ci_high=1.12, evidence=[ev]),
]
sources_happy = [
    SourceCandidate(pmid="111", title="Happy study", year=2024,
                    type_tags=["BE"], species="human", feeding="fasted")
]
cv_info_happy = CVInfo(value=20.0, source="reported", cv_source="reported",
                       confidence="high", confirmed_by_user=True)
dq_happy = compute_data_quality(pk_values_happy, ci_happy, sources_happy,
                                 cv_info_happy, [])
cv_input_happy = CVInput(cv=NumericValue(value=20.0, unit="%", evidence=[ev]),
                          confirmed=True)
design_happy = engine.select_design(
    PKExtractionResponse(inn="metformin", pk_values=pk_values_happy,
                         ci_values=ci_happy, warnings=[], missing=[],
                         validation_issues=[]),
    cv_input_happy, nti=False,
)
report_happy = {
    "inn": "metformin",
    "protocol_id": "BE-QA-HAPPY",
    "protocol_status": "Draft",
    "sources": [s.model_dump() for s in sources_happy],
    "pk_values": [p.model_dump() for p in pk_values_happy],
    "ci_values": [c.model_dump() for c in ci_happy],
    "cv_info": cv_info_happy.model_dump(),
    "data_quality": dq_happy.model_dump(),
    "design": {
        "recommendation": design_happy.design,
        "reasoning_text": design_happy.reasoning_text,
        "reasoning_rule_id": design_happy.reasoning_rule_id,
    },
    "sample_size_det": {
        "design": design_happy.design,
        "alpha": 0.05,
        "power": 0.8,
        "cv": 20.0,
        "n_total": 24,
        "n_rand": 26,
        "n_screen": 30,
        "dropout": 0.1,
        "screen_fail": 0.15,
        "warnings": [],
    },
    "reg_check": [],
    "open_questions": [],
}
try:
    path_happy = build_docx(report_happy)
    check("FullReport JSON generated", True)
    check(".docx created", os.path.exists(path_happy), f"→ {path_happy}")
    text_happy = docx_text(path_happy)
    check("DQI level green/yellow", dq_happy.level in ("green", "yellow"),
          f"level={dq_happy.level}, score={dq_happy.score}")
    check("Design output present", bool(design_happy.design),
          f"design={design_happy.design}")
    check("No 500 error", True, "(build_docx succeeded)")
except Exception as exc:
    check("Happy path FAILED with exception", False, str(exc))

# ================================================================
# QA-2  DQI Hard-Block: empty / corrupted input
# ================================================================
sep("QA-2: DQI Hard-Block (Trash Data)")
pk_empty = []
ci_empty = []
sources_empty = []
cv_none = CVInfo(value=None, source="unknown", cv_source="unknown",
                 confirmed_by_user=False)
dq_red = compute_data_quality(pk_empty, ci_empty, sources_empty, cv_none, [])

check("Pipeline did NOT crash", True, "(compute_data_quality returned)")
check("dqi.level == 'red'", dq_red.level == "red", f"level={dq_red.level}")
check("allow_n_det == False", dq_red.allow_n_det is False,
      f"allow_n_det={dq_red.allow_n_det}")
check("Hard Red reason present",
      any("Missing primary PK endpoints" in r for r in dq_red.reasons),
      f"reasons={dq_red.reasons}")

# Build .docx for red path
report_red = {
    "inn": "trash-test",
    "data_quality": dq_red.model_dump(),
    "cv_info": cv_none.model_dump(),
    "pk_values": [],
    "ci_values": [],
    "reg_check": [],
    "open_questions": [],
}
try:
    path_red = build_docx(report_red)
    check(".docx generated for red path", os.path.exists(path_red), f"→ {path_red}")
    text_red = docx_text(path_red)
    check("Hard Red Flag text in .docx",
          "Missing primary PK endpoints" in text_red,
          "")
    check("N_det not-computed note",
          "N_det" in text_red or "not computed" in text_red.lower()
          or "requires confirmed CV" in text_red.lower(),
          "")
except Exception as exc:
    check("Red-path docx FAILED", False, str(exc))

# ================================================================
# QA-3  EAEU HVD: cv_intra = 40%
# ================================================================
sep("QA-3: EAEU Regulatory Engine – HVD (cv=40%)")
pk_hvd = [
    PKValue(name="AUC0-t", value=300.0, unit="ng*h/mL", evidence=[ev]),
    PKValue(name="Cmax", value=25.0, unit="ng/mL", evidence=[ev]),
    PKValue(name="CVintra", value=40.0, unit="%", evidence=[ev]),
    PKValue(name="t1/2", value=8.0, unit="h", evidence=[ev]),
]
cv_input_hvd = CVInput(cv=NumericValue(value=40.0, unit="%", evidence=[ev]),
                        confirmed=True)
pk_json_hvd = PKExtractionResponse(
    inn="hvd_drug", pk_values=pk_hvd, ci_values=[], warnings=[], missing=[],
    validation_issues=[],
)
design_hvd = engine.select_design(pk_json_hvd, cv_input_hvd, nti=False)

check("Design is replicate or 4-way_replicate",
      "replicate" in design_hvd.design.lower(),
      f"design={design_hvd.design}")
check("HVD rule triggered",
      design_hvd.reasoning_rule_id in ("HVD", "RSABE"),
      f"rule_id={design_hvd.reasoning_rule_id}")
check("Reasoning text mentions HVD / highly variable",
      any(kw in (design_hvd.reasoning_text or "").lower()
          for kw in ("highly variable", "hvd", "replicate", "rsabe", "reference-scaled")),
      f"text={design_hvd.reasoning_text[:80]}")

# Also test RSABE at cv=55%
cv_input_rsabe = CVInput(cv=NumericValue(value=55.0, unit="%", evidence=[ev]),
                          confirmed=True)
pk_rsabe = pk_hvd.copy()
pk_rsabe[2] = PKValue(name="CVintra", value=55.0, unit="%", evidence=[ev])
pk_json_rsabe = PKExtractionResponse(
    inn="rsabe_drug", pk_values=pk_rsabe, ci_values=[], warnings=[],
    missing=[], validation_issues=[],
)
design_rsabe = engine.select_design(pk_json_rsabe, cv_input_rsabe, nti=False)
check("RSABE: 4-way_replicate at CV=55%",
      design_rsabe.design == "4-way_replicate",
      f"design={design_rsabe.design}")
check("RSABE rule_id",
      design_rsabe.reasoning_rule_id == "RSABE",
      f"rule_id={design_rsabe.reasoning_rule_id}")

# ================================================================
# QA-4  CRO Synopsis / REQUIRED_HEADINGS + None-safety
# ================================================================
sep("QA-4: CRO Synopsis Export")
print(f"  REQUIRED_HEADINGS count: {len(REQUIRED_HEADINGS)}")
required_subset = [
    "Название клинического исследования",
    "Первичные конечные точки",
    "Размер выборки",
    "Качество данных (DQI)",
]
for h in required_subset:
    check(f"Heading in REQUIRED_HEADINGS: '{h}'", h in REQUIRED_HEADINGS)

# None-safety: minimal report with lots of Nones
report_none = {
    "inn": "null-safety",
    "sources": [],
    "pk_values": [],
    "ci_values": [],
    "cv_info": None,
    "data_quality": None,
    "design": None,
    "sample_size_det": None,
    "sample_size_risk": None,
    "reg_check": [],
    "open_questions": [],
}
try:
    path_none = build_docx(report_none)
    check("None-safe .docx built", os.path.exists(path_none), f"→ {path_none}")
    text_none = docx_text(path_none)
    for h in REQUIRED_HEADINGS:
        if h not in text_none:
            check(f"Heading injected: '{h}'", False, "(missing in output)")
            break
    else:
        check("All REQUIRED_HEADINGS present in .docx", True)
except Exception as exc:
    check("None-safe docx FAILED", False, str(exc))


# ================================================================
# Summary
# ================================================================
sep("SUMMARY")
total = ok_count + fail_count
print(f"  Passed: {ok_count}/{total}")
print(f"  Failed: {fail_count}/{total}")
if fail_count:
    print("\n  ⚠️  Some checks FAILED – see details above.")
    sys.exit(1)
else:
    print("\n  🎉  ALL QA CHECKPOINTS PASSED.")
    sys.exit(0)


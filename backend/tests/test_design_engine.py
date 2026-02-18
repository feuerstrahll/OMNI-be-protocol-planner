from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import CVInput, Evidence, NumericValue, PKExtractionResponse, PKValue
from backend.services.design_engine import DesignEngine


def _load_cases() -> list[dict]:
    path = Path("docs/design_testcases.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("cases", [])


def _make_pk_json(case: dict) -> PKExtractionResponse:
    pk_values = []
    pk_cv = case.get("pk_cv")
    if pk_cv is not None:
        ev = Evidence(source_type="URL", source="calc://test", snippet="pk_cv")
        pk_values.append(PKValue(name="CVintra", value=float(pk_cv), unit="%", evidence=[ev], warnings=[]))
    return PKExtractionResponse(inn="drug", pk_values=pk_values, warnings=[], missing=[], validation_issues=[])


def _make_cv_input(case: dict) -> CVInput | None:
    cv_data = case.get("cv_input")
    if not cv_data:
        return None
    ev = Evidence(source_type="URL", source="calc://test", snippet="cv_input")
    return CVInput(
        cv=NumericValue(value=float(cv_data["value"]), unit="%", evidence=[ev]),
        confirmed=bool(cv_data.get("confirmed")),
    )


def test_design_engine_cases():
    engine = DesignEngine("backend/rules/design_rules.yaml")
    cases = _load_cases()
    assert cases, "No design test cases found in docs/design_testcases.json"

    for case in cases:
        pk_json = _make_pk_json(case)
        cv_input = _make_cv_input(case)
        res = engine.select_design(pk_json, cv_input, case.get("nti"))
        expected = case["expected"]

        assert res.design == expected["design"], f"{case.get('name')} design mismatch"
        assert res.reasoning_rule_id == expected["rule_id"], f"{case.get('name')} rule id mismatch"
        assert res.reasoning_text, f"{case.get('name')} missing reasoning text"

        expected_missing = expected.get("missing", [])
        for missing in expected_missing:
            assert missing in res.required_inputs_missing, f"{case.get('name')} missing '{missing}'"

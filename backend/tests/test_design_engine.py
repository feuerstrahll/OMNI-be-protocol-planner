from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.schemas import CVInput, Evidence, NumericValue, PKExtractionResponse, PKValue
from backend.services.design_engine import DesignEngine


def _load_cases() -> list[dict]:
    path = Path("docs/design_testcases.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("cases", [])


def _parse_preconditions(text: str | None) -> tuple[float | None, float | None, bool | None, bool]:
    if not text:
        return None, None, None, False
    lower = text.lower()
    unsupported = [
        "carryover",
        "modified release",
        "dropout",
        "formulation",
        "steady state",
        "multiple formulations",
        "food effect",
        "fed_state_effect",
        "multiple dose",
    ]
    if any(key in lower for key in unsupported):
        return None, None, None, True

    cv = None
    t_half = None
    nti = None

    match = re.search(r"cv_intra\\s*=\\s*([0-9]+(?:\\.[0-9]+)?)", text, re.IGNORECASE)
    if match:
        cv = float(match.group(1))

    match = re.search(r"(half[_ ]life|t1/2)\\s*=\\s*([0-9]+(?:\\.[0-9]+)?)\\s*h", text, re.IGNORECASE)
    if match:
        t_half = float(match.group(2))

    if re.search(r"nti\\s*=\\s*true", text, re.IGNORECASE):
        nti = True
    elif "not nti" in lower or re.search(r"nti\\s*=\\s*false", text, re.IGNORECASE):
        nti = False

    return cv, t_half, nti, False


def _expected_tag(expected: str) -> str | None:
    lower = expected.lower()
    if "replicate" in lower:
        return "replicate"
    if "parallel" in lower:
        return "parallel"
    if "2x2" in lower or "2×2" in lower:
        return "2x2"
    return None


def test_design_engine_cases():
    engine = DesignEngine("backend/rules/design_rules.yaml")
    cases = _load_cases()
    assert cases, "No design test cases found in docs/design_testcases.json"

    for case in cases:
        pre = case.get("preconditions", "")
        cv, t_half, nti, skip = _parse_preconditions(pre)
        if skip:
            pytest.skip(f"Skipping unsupported preconditions: {pre}")

        expected = case.get("expected", "")
        tag = _expected_tag(expected)
        if tag is None:
            pytest.skip(f"Skipping non-decisive expected text: {expected}")

        pk_values: list[PKValue] = []
        ev = Evidence(source_type="URL", source="calc://test", snippet="test")
        if cv is not None:
            pk_values.append(PKValue(name="CVintra", value=cv, unit="%", evidence=[ev], warnings=[]))
        if t_half is not None:
            pk_values.append(PKValue(name="t1/2", value=t_half, unit="h", evidence=[ev], warnings=[]))

        pk_json = PKExtractionResponse(
            inn="drug",
            pk_values=pk_values,
            warnings=[],
            missing=[],
            validation_issues=[],
        )

        cv_input = None
        if cv is not None:
            cv_input = CVInput(
                cv=NumericValue(value=float(cv), unit="%", evidence=[ev]),
                confirmed=True,
            )

        res = engine.select_design(pk_json, cv_input, nti)
        design_lower = res.design.lower()

        if tag == "replicate":
            assert "replicate" in design_lower, f"{case.get('name')} design mismatch"
        elif tag == "parallel":
            assert "parallel" in design_lower, f"{case.get('name')} design mismatch"
        elif tag == "2x2":
            assert "2x2" in design_lower or "2×2" in design_lower, f"{case.get('name')} design mismatch"

        assert res.reasoning_text, f"{case.get('name')} missing reasoning text"

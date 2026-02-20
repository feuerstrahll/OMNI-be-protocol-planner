from __future__ import annotations

import json
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


def _build_pk_json(inputs: dict) -> tuple[PKExtractionResponse, CVInput | None]:
    pk_values = []
    cv_input = None
    evidence = [Evidence(source_type="URL", source="manual://test", snippet="test")]

    cv = inputs.get("cv") if "cv" in inputs else inputs.get("CV")
    if cv is not None:
        pk_values.append(PKValue(name="CVintra", value=float(cv), unit="%", evidence=evidence))
        cv_input = CVInput(cv=NumericValue(value=float(cv), unit="%", evidence=evidence), confirmed=True)

    cmax = inputs.get("Cmax")
    if cmax is not None:
        pk_values.append(PKValue(name="Cmax", value=float(cmax), unit="ng/mL", evidence=evidence))

    auc = inputs.get("AUC")
    if auc is not None:
        pk_values.append(PKValue(name="AUC0-t", value=float(auc), unit="ng*h/mL", evidence=evidence))

    t12 = inputs.get("t12") if "t12" in inputs else inputs.get("t_half")
    if t12 is not None:
        pk_values.append(PKValue(name="t1/2", value=float(t12), unit="h", evidence=evidence))

    pk_json = PKExtractionResponse(
        inn="drug",
        pk_values=pk_values,
        ci_values=[],
        warnings=[],
        missing=[],
        validation_issues=[],
    )
    return pk_json, cv_input


def test_design_testcases_from_docs():
    engine = DesignEngine("backend/rules/design_rules.yaml")
    cases = _load_cases()
    assert cases, "No design test cases found in docs/design_testcases.json"

    allowed_designs = {"2x2_crossover", "replicate", "4-way_replicate", "parallel", "unknown"}

    for case in cases:
        inputs = case.get("inputs") or {}
        expected = case.get("expected_outputs") or {}
        expected_design = expected.get("recommended_design")
        note = case.get("notes") or case.get("name") or case.get("id") or "case"

        pk_json, cv_input = _build_pk_json(inputs)
        nti = inputs.get("nti")
        result = engine.select_design(pk_json, cv_input, nti=nti)

        if expected_design is None:
            assert result.design, f"{note}: design is empty"
            assert result.design in allowed_designs, f"{note}: unexpected design {result.design}"
        else:
            assert result.design == expected_design, f"{note}: expected {expected_design}, got {result.design}"

        n_det_expected = expected.get("n_det")
        if n_det_expected == []:
            # Design engine doesn't expose N_det; assert nothing beyond design.
            pass

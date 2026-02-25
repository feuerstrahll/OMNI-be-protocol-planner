"""
Test that use_fallback=false is respected: no fallback PK/CV, no mock injection.
"""
import json
import os
import tempfile

import pytest

from backend.schemas import (
    CIValue,
    CVInfo,
    RunPipelineRequest,
    SourceCandidate,
)
from backend.services.cv_gate import select_cv_info, _derive_from_ci, _fallback_allowed
from backend.services.data_quality import compute_data_quality, _maybe_load_mock
from backend.services.variability_model import VariabilityModel


def test_fallback_allowed_respects_false():
    assert _fallback_allowed(False) is False
    assert _fallback_allowed(True) is True


def test_cv_gate_no_fallback_when_use_fallback_false(monkeypatch):
    """When use_fallback=False, CVfromCI approximation must NOT be used (PowerTOST unavailable)."""
    monkeypatch.setattr(
        "backend.services.cv_gate.powertost_health",
        lambda: {"powertost_ok": False},
    )
    ci = CIValue(
        param="AUC",
        ci_low=0.90,
        ci_high=1.10,
        n=24,
        confidence_level=0.90,
        design_hint="2x2_crossover; log_transformed",
    )
    cv_info, questions = _derive_from_ci(ci, cv_confirmed=False, use_fallback=False)
    assert cv_info.value is None
    assert any("PowerTOST" in q.question for q in questions)
    assert "approximation_for_testing_only" not in (cv_info.warnings or [])


def test_cv_gate_fallback_when_use_fallback_true(monkeypatch):
    """When use_fallback=True, approximation is used when PowerTOST unavailable."""
    monkeypatch.setattr(
        "backend.services.cv_gate.powertost_health",
        lambda: {"powertost_ok": False},
    )
    ci = CIValue(
        param="AUC",
        ci_low=0.90,
        ci_high=1.10,
        n=24,
        confidence_level=0.90,
        design_hint="2x2_crossover; log_transformed",
    )
    cv_info, _ = _derive_from_ci(ci, cv_confirmed=False, use_fallback=True)
    assert cv_info.value is not None
    assert "approximation_for_testing_only" in (cv_info.warnings or [])


def test_data_quality_no_mock_when_use_fallback_false():
    """When use_fallback=False, empty pk/ci must NOT trigger mock load."""
    pk_empty = []
    ci_empty = []
    eval_pk, eval_ci, mock_used = _maybe_load_mock(
        pk_empty, ci_empty, use_mock_extractor=False, use_fallback=False, mock_path="docs/mock_extractor_output.json"
    )
    assert mock_used is False
    assert eval_pk == []
    assert eval_ci == []


def test_data_quality_mock_when_use_fallback_true_and_empty():
    """When use_fallback=True and empty pk/ci, mock is loaded (legacy behavior)."""
    mock_data = {
        "pk_values": [
            {"name": "Cmax", "value": 10.0, "unit": "ng/mL", "evidence": [], "warnings": []},
            {"name": "AUC0-t", "value": 100.0, "unit": "ng*h/mL", "evidence": [], "warnings": []},
        ],
        "ci_values": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(mock_data, f)
        mock_path = f.name
    try:
        pk_empty = []
        ci_empty = []
        eval_pk, eval_ci, mock_used = _maybe_load_mock(
            pk_empty, ci_empty, use_mock_extractor=False, use_fallback=True, mock_path=mock_path
        )
        assert mock_used is True
        assert len(eval_pk) > 0
        assert any(p.name == "Cmax" for p in eval_pk)
    finally:
        os.unlink(mock_path)


def test_data_quality_no_fallback_evidence_in_report_when_use_fallback_false():
    """DQI with use_fallback=False on empty data: no fallback:// evidence in components."""
    pk_empty = []
    ci_empty = []
    sources = []
    cv_none = CVInfo(value=None, source="unknown", cv_source="unknown", confirmed_by_user=False)
    dq = compute_data_quality(
        pk_empty, ci_empty, sources, cv_none, [], use_mock_extractor=False, use_fallback=False
    )
    assert dq.level == "red"
    assert dq.allow_n_det is False
    assert not any("fallback" in r.lower() for r in (dq.reasons or []) if "mock" not in r.lower())


def test_run_pipeline_includes_run_id_and_request_hash(monkeypatch):
    """Pipeline report includes run_id and request_hash for audit/correlation."""
    from backend.services.pipeline import run_pipeline

    class MockPubMed:
        def search_sources(self, inn, retmax, mode="be"):
            return "", [SourceCandidate(id_type="PMID", id="123", title="Test", year=2020)], []

        def resolve_sources(self, ref_ids, inn):
            return [SourceCandidate(id_type="PMID", id="123", title="Test", year=2020)], []

        def get_official_sources(self, inn):
            return []

        def fetch_abstracts(self, ref_ids):
            return {"PMID:123": "No Cmax or AUC here."}

    class MockExtractor:
        def extract(self, abstracts, inn=None):
            return [], [], ["Cmax", "AUC0-t"]

    report, _ = run_pipeline(
        RunPipelineRequest(inn="testdrug", use_fallback=False, selected_sources=["PMID:123"]),
        pubmed_client=MockPubMed(),
        pk_extractor=MockExtractor(),
        validator=__import__("backend.services.validator", fromlist=["PKValidator"]).PKValidator(
            "backend/rules/validation_rules.yaml"
        ),
        design_engine=__import__("backend.services.design_engine", fromlist=["DesignEngine"]).DesignEngine(
            "backend/rules/design_rules.yaml"
        ),
        variability_model=VariabilityModel("backend/rules/variability_rules.yaml"),
        reg_checker=__import__("backend.services.reg_checker", fromlist=["RegChecker"]).RegChecker(
            "backend/rules/reg_rules.yaml"
        ),
    )
    assert report.run_id is not None
    assert len(report.run_id) == 36
    assert report.request_hash is not None
    assert len(report.request_hash) == 16

import pytest
from fastapi import HTTPException

from backend import api
from backend.schemas import (
    CVInfo,
    DataQuality,
    DataQualityComponents,
    FullReport,
    RunPipelineRequest,
    SampleSizeSummary,
    SynopsisCompleteness,
    RegCheckSummary,
)


def _minimal_report() -> FullReport:
    return FullReport(
        inn="X",
        sources=[],
        pk_values=[],
        ci_values=[],
        study_condition="unknown",
        reg_check=[],
        open_questions=[],
        audit_trail=[],
        cv_info=CVInfo(),
        data_quality=DataQuality(components=DataQualityComponents()),
        sample_size=SampleSizeSummary(),
        reg_check_summary=RegCheckSummary(),
        synopsis_completeness=SynopsisCompleteness(),
    )


def test_run_pipeline_final_returns_422_on_blockers(monkeypatch):
    def fake_run_pipeline(req, **kwargs):
        return _minimal_report(), ["N_not_computed"]

    monkeypatch.setattr(api, "run_pipeline_service", fake_run_pipeline)

    req = RunPipelineRequest(inn="x", output_mode="final")
    with pytest.raises(HTTPException) as excinfo:
        api.run_pipeline(req)
    assert excinfo.value.status_code == 422
    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail.get("blockers") == ["N_not_computed"]


def test_run_pipeline_draft_returns_report(monkeypatch):
    def fake_run_pipeline(req, **kwargs):
        return _minimal_report(), ["anything"]

    monkeypatch.setattr(api, "run_pipeline_service", fake_run_pipeline)

    req = RunPipelineRequest(inn="x", output_mode="draft")
    report = api.run_pipeline(req)
    assert isinstance(report, FullReport)

import os

from backend.services.docx_builder import build_docx


def _minimal_report(inn: str) -> dict:
    return {
        "inn": inn,
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


def test_docx_with_minimal_report():
    report = _minimal_report("demo-minimal")
    path = build_docx(report)
    assert os.path.exists(path)


def test_docx_with_missing_cv_and_pk():
    report = _minimal_report("demo-missing-cv")
    report["pk_values"] = []
    report["cv_info"] = None
    path = build_docx(report)
    assert os.path.exists(path)


def test_docx_with_only_risk_present():
    report = _minimal_report("demo-risk-only")
    report["sample_size_risk"] = {
        "cv_distribution": "triangular",
        "n_targets": {"0.7": 24, "0.8": 28, "0.9": 32},
        "p_success_at_n": {"0.7": 0.7, "0.8": 0.8, "0.9": 0.9},
        "sensitivity_notes": ["demo"],
        "warnings": [],
        "seed": 123,
        "n_sims": 1000,
        "rng_name": "PCG64",
        "method": "mc",
        "numpy_version": "1.26.0",
    }
    path = build_docx(report)
    assert os.path.exists(path)

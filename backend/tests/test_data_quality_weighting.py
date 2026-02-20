from backend.schemas import CIValue, CVInfo, Evidence, PKValue, SourceCandidate
from backend.services.data_quality import compute_data_quality


def _make_pk_values(with_evidence: bool):
    evidence = [Evidence(source_type="URL", source="manual://test", snippet="x")] if with_evidence else []
    return [
        PKValue(name="Cmax", value=10.0, unit="ng/mL", evidence=list(evidence)),
        PKValue(name="AUC0-t", value=100.0, unit="ng*h/mL", evidence=list(evidence)),
        PKValue(name="AUC0-inf", value=120.0, unit="ng*h/mL", evidence=list(evidence)),
        PKValue(name="t1/2", value=4.0, unit="h", evidence=list(evidence)),
    ]


def test_dqi_traceability_weighting():
    sources = [
        SourceCandidate(
            pmid="123",
            title="Test",
            year=2020,
            type_tags=["BE"],
            species="human",
            feeding="fasted",
            url="https://example.com",
        )
    ]
    ci_values = [
        CIValue(
            param="AUC",
            ci_low=0.9,
            ci_high=1.1,
            evidence=[Evidence(source_type="URL", source="manual://ci", snippet="ci")],
        )
    ]
    cv_info = CVInfo(value=20.0, confirmed_by_user=True, source="reported")

    dq_with = compute_data_quality(_make_pk_values(True), ci_values, sources, cv_info, [])
    dq_without = compute_data_quality(_make_pk_values(False), ci_values, sources, cv_info, [])

    assert dq_with.score > dq_without.score
    assert dq_with.components.traceability > dq_without.components.traceability
    assert (dq_with.score - dq_without.score) >= 20

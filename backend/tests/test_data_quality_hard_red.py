from backend.schemas import CVInfo, Evidence, PKValue, SourceCandidate
from backend.services.data_quality import compute_data_quality


def test_dqi_hard_red_missing_primary_endpoints():
    pk_values = [
        PKValue(
            name="t1/2",
            value=12.0,
            unit="h",
            evidence=[Evidence(source_type="URL", source="manual://test", snippet="x")],
        )
    ]
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
    cv_info = CVInfo(value=20.0, confirmed_by_user=True, source="reported")

    dq = compute_data_quality(pk_values, [], sources, cv_info, [])

    assert dq.score == 0
    assert dq.level == "red"
    assert dq.allow_n_det is False
    assert dq.reasons and dq.reasons[0] == "Hard Red Flag: Missing primary PK endpoints (AUC and Cmax)."

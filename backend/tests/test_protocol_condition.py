import backend.api as api
from backend.schemas import RunPipelineRequest


def test_protocol_condition_persisted(monkeypatch):
    def fake_search_sources(inn: str, retmax: int):
        return "", [], []

    monkeypatch.setattr(api.pubmed_client, "search_sources", fake_search_sources)

    req = RunPipelineRequest(inn="test", protocol_condition="fasted")
    report = api.run_pipeline(req)
    assert report.protocol_condition == "fasted"

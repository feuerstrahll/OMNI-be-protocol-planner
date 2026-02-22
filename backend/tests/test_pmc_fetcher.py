import types

from backend.services.pmc_fetcher import build_snippets, fetch_pmc_sections


class DummyResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else text
        self.status_code = status_code


def _mock_get(xml: str):
    def _inner(url, params=None, timeout=None):
        return DummyResp(xml, 200)

    return _inner


def test_pmc_fetcher_extracts_sections_and_tables(monkeypatch):
    xml = """
    <article>
      <body>
        <sec sec-type="results">
          <title>Results</title>
          <p>Cmax was 10 ng/mL and CV was 25%.</p>
        </sec>
        <table-wrap>
          <label>Table 2</label>
          <caption><title>PK parameters</title></caption>
          <table>
            <tr><th>Param</th><th>Value</th></tr>
            <tr><td>CV</td><td>30%</td></tr>
          </table>
          <table-wrap-foot><p>foot note</p></table-wrap-foot>
        </table-wrap>
      </body>
      <back><ref-list><p>Should be ignored</p></ref-list></back>
    </article>
    """
    monkeypatch.setattr("requests.get", _mock_get(xml))
    data = fetch_pmc_sections("PMCID:123")
    assert isinstance(data, dict)
    # snippets should catch CV
    assert "CV" in data["snippets_text"]
    # target_text should include results/table but not references
    assert "Table 2" in data["target_text"]
    assert "Should be ignored" not in data["full_text"]


def test_build_snippets_limits_and_locations():
    sections = [{"title": "Results", "text": "CV 20% and CI 0.9-1.1. " * 50}]
    tables = [{"label": "Table 1", "as_text": "Cmax 100 ng/mL CV 30%", "caption": "", "header": "", "foot": ""}]
    snips = build_snippets(sections, tables, source_id="PMCID:1")
    assert 1 <= len(snips) <= 20
    assert all("location" in s and s["location"].startswith(("sec:", "table:")) for s in snips)

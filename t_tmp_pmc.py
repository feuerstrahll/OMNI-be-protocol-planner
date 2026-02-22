import requests
from backend.services import pmc_fetcher as pf

xml = """
<article>
  <body>
    <sec sec-type='results'>
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
class Dummy:
    def __init__(self, text, status_code=200):
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else text
        self.status_code = status_code

def _mock_get(url, params=None, timeout=None):
    return Dummy(xml,200)

requests.get = _mock_get
print(pf.fetch_pmc_sections('PMCID:123'))

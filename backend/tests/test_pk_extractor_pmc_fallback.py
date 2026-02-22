from backend.services.pk_extractor import PKExtractor
from backend.schemas import PKValue, CIValue


class DummyLLM:
    def extract_pk_from_text(self, text, inn="", source_id="", location=""):
        # Simulate parse failure / empty extraction
        return {}


def _dummy_pmc_payload():
    row = "Cmax 80.00 - 125.00 20.41"
    return {
        "snippets_text": "",
        "target_text": "",
        "full_text": f"\n{row}\n",
        "supplementary_present": False,
        "warnings": [],
    }


def test_pmc_regex_fallback_cv_extracted():
    def fake_pmc_fetcher(source_id: str):
        return _dummy_pmc_payload()

    extractor = PKExtractor(llm_client=DummyLLM(), pmc_fetcher=fake_pmc_fetcher, llm_extractor=None)
    pk_values, ci_values, missing = extractor.extract({"PMCID:10175790": "dummy"}, inn="test")

    cv_vals = [pk.value for pk in pk_values if pk.name == "CVintra"]
    assert cv_vals, "CVintra should be extracted via regex fallback"
    assert abs(cv_vals[0] - 20.41) < 1e-6

    # CI fallback is optional; if present, ensure it matches row
    ci_match = [ci for ci in ci_values if isinstance(ci, CIValue)]
    if ci_match:
        ci = ci_match[0]
        assert ci.ci_type == "percent"
        assert abs(ci.ci_low - 80.0) < 1e-6
        assert abs(ci.ci_high - 125.0) < 1e-6

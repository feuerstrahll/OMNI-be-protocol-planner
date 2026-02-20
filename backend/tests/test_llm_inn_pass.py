from backend.services.pk_extractor import PKExtractor


class DummyLLM:
    def __init__(self) -> None:
        self.calls = []

    def extract(self, inn: str, pmid: str, abstract_text: str):
        self.calls.append(inn)
        return {"pk_values": [], "ci_values": []}


def test_llm_receives_inn_when_provided():
    dummy = DummyLLM()
    extractor = PKExtractor(llm_extractor=dummy)
    abstracts = {"123": "Cmax = 10 ng/mL"}
    extractor.extract(abstracts, inn="metformin")
    assert dummy.calls[-1] == "metformin"


def test_llm_defaults_to_empty_inn():
    dummy = DummyLLM()
    extractor = PKExtractor(llm_extractor=dummy)
    abstracts = {"123": "Cmax = 10 ng/mL"}
    extractor.extract(abstracts)
    assert dummy.calls[-1] == ""

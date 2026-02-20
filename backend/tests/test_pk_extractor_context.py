from backend.services.pk_extractor import PKExtractor


def test_pk_extractor_regex_fallback_without_llm():
    extractor = PKExtractor()
    abstracts = {"123": "Subjects were fasted. Cmax = 10 ng/mL."}
    pk_values, ci_values, missing = extractor.extract(abstracts)
    assert any(pk.name == "Cmax" for pk in pk_values)


def test_fed_fasted_detection_and_meal_warning():
    extractor = PKExtractor()
    abstracts = {"124": "Subjects were in the fasted state. Cmax = 10 ng/mL."}
    extractor.extract(abstracts)
    assert extractor.last_context.get("study_condition") == "fasted"

    abstracts_fed = {"125": "Subjects were in the fed state. Cmax = 12 ng/mL."}
    extractor.extract(abstracts_fed)
    assert extractor.last_context.get("study_condition") == "fed"
    assert "clarify_meal_composition" in (extractor.last_warnings or [])

from backend.api import _filter_pk_ci_for_calculation
from backend.schemas import PKExtractionResponse
from backend.services.cv_gate import select_cv_info
from backend.services.pk_extractor import PKExtractor
from backend.services.reg_checker import RegChecker
from backend.services.variability_model import VariabilityModel


def test_feeding_conflict_clarify_and_exclude():
    extractor = PKExtractor()
    abstracts = {
        "123": (
            "Subjects were fasted and fed in a 2x2 crossover log-transformed study. "
            "Cmax = 10 ng/mL. 90% CI 0.9-1.1 n=24."
        )
    }
    pk_values, ci_values, missing = extractor.extract(abstracts)

    pk_json = PKExtractionResponse(
        inn="test",
        pk_values=pk_values,
        ci_values=ci_values,
        warnings=extractor.last_warnings,
        missing=missing,
        validation_issues=[],
    )

    reg_checker = RegChecker("backend/rules/reg_rules.yaml")
    reg_resp = reg_checker.run("2x2 crossover", pk_json, schedule_days=None, cv_input=None)
    assert any(
        check.rule_id == "FEEDING_CONDITION_CLARIFY" and check.status == "CLARIFY"
        for check in reg_resp.checks
    )

    pk_calc, ci_calc, _ = _filter_pk_ci_for_calculation(pk_values, ci_values, None)
    assert all(not getattr(ci, "ambiguous_condition", False) for ci in ci_calc)

    variability_model = VariabilityModel("backend/rules/variability_rules.yaml")
    pk_json_calc = PKExtractionResponse(
        inn="test",
        pk_values=pk_calc,
        ci_values=ci_calc,
        warnings=[],
        missing=[],
        validation_issues=[],
    )
    cv_info, _ = select_cv_info(pk_json_calc, ci_calc, None, False, variability_model)
    assert cv_info.cv_source != "derived_from_ci"

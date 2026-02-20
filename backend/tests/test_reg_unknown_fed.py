from backend.schemas import PKExtractionResponse
from backend.services.reg_checker import RegChecker


def test_reg_check_unknown_fed_condition():
    pk_json = PKExtractionResponse(
        inn="drug",
        pk_values=[],
        ci_values=[],
        study_condition="unknown",
        warnings=[],
        missing=[],
        validation_issues=[],
    )
    reg_checker = RegChecker("backend/rules/reg_rules.yaml")
    resp = reg_checker.run("2x2 crossover", pk_json, schedule_days=None, cv_input=None)

    assert any(
        check.rule_id == "REG-008" and check.status == "CLARIFY" for check in resp.checks
    )

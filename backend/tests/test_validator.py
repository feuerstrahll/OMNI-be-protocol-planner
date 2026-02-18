from backend.schemas import Evidence, PKValue
from backend.services.validator import PKValidator


def test_validator_flags_out_of_range():
    validator = PKValidator("backend/rules/validation_rules.yaml")
    pk = PKValue(
        name="Cmax",
        value=1e9,
        unit="ng/mL",
        evidence=[Evidence(source_type="URL", source="calc://test", snippet="x")],
    )
    issues = validator.validate([pk])
    assert any(i.severity == "WARN" for i in issues)

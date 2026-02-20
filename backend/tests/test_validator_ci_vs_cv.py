import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.schemas import CIValue, Evidence, PKValue
from backend.services.validator import PKValidator


def _make_cv(value: float) -> PKValue:
    return PKValue(
        name="CVintra",
        value=value,
        unit="%",
        evidence=[Evidence(source_type="URL", source="calc://test", snippet="cv")],
    )


def test_ci_conflicts_with_cv_triggers_warning():
    validator = PKValidator("backend/rules/validation_rules.yaml")
    pk = _make_cv(22)
    ci = CIValue(param="Cmax", ci_low=0.95, ci_high=1.05, ci_type="ratio", n=18, design_hint="2x2")

    issues, warnings = validator.validate_with_warnings([pk], [ci])

    assert any("conflict" in i.message.lower() for i in issues)
    assert any("ci_vs_cv" in w for w in warnings)


def test_ci_consistent_with_cv_no_warning():
    validator = PKValidator("backend/rules/validation_rules.yaml")
    pk = _make_cv(22)
    # Consistent CI width for CV~22% and n=18 is roughly 0.88–1.13
    ci = CIValue(param="Cmax", ci_low=0.88, ci_high=1.13, ci_type="ratio", n=18, design_hint="2x2")

    issues, warnings = validator.validate_with_warnings([pk], [ci])

    assert not any("conflict" in i.message.lower() for i in issues)
    assert not any("ci_vs_cv" in w for w in warnings)

from backend.schemas import (
    CVInput,
    Evidence,
    NumericValue,
    PKExtractionResponse,
    PKValue,
)
from backend.services.design_engine import DesignEngine


def test_design_by_cv_low():
    engine = DesignEngine("backend/rules/design_rules.yaml")
    pk = PKExtractionResponse(inn="drug", pk_values=[], warnings=[], missing=[], validation_issues=[])
    cv = CVInput(
        cv=NumericValue(value=25, unit="%", evidence=[Evidence(source_type="URL", source="calc://test", snippet="x")]),
        confirmed=True,
    )
    res = engine.select_design(pk, cv, nti=False)
    assert "2x2" in res.design

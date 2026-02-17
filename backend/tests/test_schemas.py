import pytest

from backend.schemas import Evidence, NumericValue, PKValue


def test_numeric_value_requires_evidence():
    ev = Evidence(source_type="URL", source="calc://test", snippet="x")
    val = NumericValue(value=1.23, unit="%", evidence=[ev])
    assert val.value == 1.23


def test_pk_value_model():
    ev = Evidence(source_type="URL", source="calc://test", snippet="x")
    val = NumericValue(value=10, unit="ng/mL", evidence=[ev])
    pk = PKValue(metric="Cmax", value=val)
    assert pk.metric == "Cmax"

import pytest

from backend.schemas import Evidence, PKValue


def test_numeric_value_requires_evidence():
    ev = Evidence(source_type="URL", source="calc://test", snippet="x")
    pk = PKValue(name="Cmax", value=1.23, unit="ng/mL", evidence=[ev])
    assert pk.value == 1.23


def test_pk_value_model():
    ev = Evidence(source_type="URL", source="calc://test", snippet="x")
    pk = PKValue(name="Cmax", value=10, unit="ng/mL", evidence=[ev])
    assert pk.name == "Cmax"

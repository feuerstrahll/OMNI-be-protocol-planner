import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.schemas import CIValue, CVInfo, DataQuality, Evidence, PKValue, SourceCandidate, ValidationIssue
from backend.services.data_quality import compute_data_quality
from backend.services.validator import PKValidator


def _load_row(idx: int) -> dict:
    path = Path("docs/golden_set.csv")
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows[idx]


def _make_pk_values(row: dict) -> list[PKValue]:
    evidence = [Evidence(source_type="PMID", source=row["PMID"], snippet="golden set")]
    return [
        PKValue(name="Cmax", value=float(row["expected_Cmax"]), unit="ng/mL", evidence=evidence),
        PKValue(name="AUC(0-t)", value=float(row["expected_AUC"]), unit="ng*h/mL", evidence=evidence),
        PKValue(name="t1/2", value=float(row["expected_t12"]), unit="h", evidence=evidence),
        PKValue(
            name="CVintra",
            value=float(row["expected_CV"]) if row["expected_CV"] else None,
            unit="%",
            evidence=evidence,
        ),
    ]


def _make_ci(row: dict, param: str = "Cmax") -> CIValue:
    evidence = [Evidence(source_type="PMID", source=row["PMID"], snippet="golden set CI")]
    return CIValue(
        param=param,
        ci_low=float(row["CI_low"]),
        ci_high=float(row["CI_high"]),
        ci_type="ratio",
        n=int(row["n"]),
        design_hint="2x2",
        evidence=evidence,
    )


def _make_source(row: dict) -> SourceCandidate:
    return SourceCandidate(
        pmid=row["PMID"],
        title="golden-set-entry",
        type_tags=["BE"],
        species="human",
        feeding="fasted",
    )


def _dq_from_row(row_idx: int) -> tuple[DataQuality, list[ValidationIssue], list[str]]:
    row = _load_row(row_idx)
    pk_values = _make_pk_values(row)
    ci = _make_ci(row)
    sources = [_make_source(row)]
    validator = PKValidator("backend/rules/validation_rules.yaml")
    validation_issues, validation_warnings = validator.validate_with_warnings(pk_values, [ci])

    cv_val = float(row["expected_CV"]) if row["expected_CV"] else None
    cv_info = CVInfo(
        value=cv_val,
        source="reported" if cv_val is not None else "unknown",
        cv_source="reported" if cv_val is not None else "unknown",
        confirmed_by_user=True,
        evidence=pk_values[0].evidence,
    )

    dq = compute_data_quality(
        pk_values,
        [ci],
        sources,
        cv_info,
        validation_issues,
    )
    return dq, validation_issues, validation_warnings


def test_golden_row1_happy_path_is_green():
    dq, issues, _ = _dq_from_row(0)  # row index 0 after header -> line 1
    assert dq.level == "green"
    assert all(i.severity != "ERROR" for i in issues)
    assert not any("conflict" in i.message.lower() for i in issues)


def test_golden_row5_ci_vs_cv_penalized():
    dq, issues, warnings = _dq_from_row(4)  # zero-based index 4 -> CSV line 5
    assert any("conflict" in i.message.lower() for i in issues)
    assert any("ci_vs_cv" in w for w in warnings)
    # Consistency should be penalized (<1.0) due to conflicting_values
    assert dq.components.consistency < 1.0
    # Level may remain green depending on weights, but penalty should be recorded
    assert any("Penalty on consistency" in r for r in dq.reasons)

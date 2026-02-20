# Integration Report — Biologists Deliverables

Date: 2026-02-20  
Repo: `feuerstrahll/OMNI-be-protocol-planner`

## A) File Operations Log

| Old Path | New Path | Action | Notes |
| --- | --- | --- | --- |
| `backend/rules/design_rules.yaml` | `backend/rules/design_rules.yaml` | archived+replaced | archived to `backend/rules/_archive/design_rules.yaml.20260220-1541` |
| `backend/rules/reg_rules.yaml` | `backend/rules/reg_rules.yaml` | archived+replaced | archived to `backend/rules/_archive/reg_rules.yaml.20260220-1541` |
| `backend/rules/validation_rules.yaml` | `backend/rules/validation_rules.yaml` | archived+replaced | archived to `backend/rules/_archive/validation_rules.yaml.20260220-1541` |
| `backend/rules/variability_rules.yaml` | `backend/rules/variability_rules.yaml` | archived+replaced | archived to `backend/rules/_archive/variability_rules.yaml.20260220-1541` |
| `docs/design_testcases.json` | `docs/design_testcases.json` | archived+replaced | archived to `docs/_archive/design_testcases.json.20260220-1541` |
| `docs/CV_DISTRIBUTION.md` | `docs/CV_DISTRIBUTION.md` | archived+replaced | archived to `docs/_archive/CV_DISTRIBUTION.md.20260220-1541` |
| `docs/PK_EXTRACTION_SPEC.md` | `docs/PK_EXTRACTION_SPEC.md` | archived+replaced | archived to `docs/_archive/PK_EXTRACTION_SPEC.md.20260220-1541` |
| `docs/DESIGN_PARAMETER_PACK.md` | `docs/DESIGN_PARAMETER_PACK.md` | archived+replaced | archived to `docs/_archive/DESIGN_PARAMETER_PACK.md.20260220-1541` |
| `docs/POWERTOST_MAPPING.md` | `docs/POWERTOST_MAPPING.md` | archived+replaced | archived to `docs/_archive/POWERTOST_MAPPING.md.20260220-1541` |
| `docs/DATA_QUALITY_CRITERIA.md` | `docs/DATA_QUALITY_CRITERIA.md` | archived+replaced | archived to `docs/_archive/DATA_QUALITY_CRITERIA.md.20260220-1541` |
| `docs/OPEN_QUESTIONS_LIBRARY.md` | `docs/OPEN_QUESTIONS_LIBRARY.md` | archived+replaced | archived to `docs/_archive/OPEN_QUESTIONS_LIBRARY.md.20260220-1541` |
| `docs/SYNOPSIS_PHRASES.md` | `docs/SYNOPSIS_PHRASES.md` | archived+replaced | archived to `docs/_archive/SYNOPSIS_PHRASES.md.20260220-1541` |
| `docs/golden_set.csv` | `docs/golden_set.csv` | archived+replaced | archived to `docs/_archive/golden_set.csv.20260220-1541`; header normalized |

## B) Content Changes Summary

**backend/rules/design_rules.yaml**
- Replaced legacy rule list with biologist `baseline_design` + `drivers` + `classification_rules`.
- Design selection now prioritizes drivers (HVD/NTI/long t½) and falls back to baseline design.

**backend/rules/reg_rules.yaml**
- Replaced legacy checks with YAML-driven `rules[]` using `when` conditions and `decision/message/what_to_clarify`.
- Introduced DQI-driven gating rule (REG-001), CVfromCI assumption rule (REG-003), NTI rule (REG-004), HVD rule (REG-005), long t½/carryover rule (REG-006), and source conflict rule (REG-007).

**backend/rules/validation_rules.yaml**
- Replaced with new `units`, `ranges`, `conversions`, `logic_checks`, and `warnings` fields.
- Validator now derives normalization and warning rules from this file.

**backend/rules/variability_rules.yaml**
- Replaced with baseline CV range + driver adjustments (BCS class, first-pass, CYP polymorphism, food effect).

**docs/DATA_QUALITY_CRITERIA.md**
- New weights and thresholds reflected in code defaults: C 0.25, T 0.25, P 0.20, K 0.20, S 0.10; Yellow ≥55; Green ≥80.

**docs/design_testcases.json**
- Replaced with narrative testcases. Tests now parse preconditions (CV_intra, half_life, NTI) and verify expected design keywords.

**docs/golden_set.csv**
- Converted from biologists’ file and normalized header to:
  `PMID,expected_Cmax,expected_AUC,expected_t12,expected_CV,CI_low,CI_high,n,notes`
- `design` column preserved in `notes` as `design=...`.

## C) How Logic Works Now (Scenarios)

1) **CV present & confirmed → N_det enabled**
   - CV source from reported/manual/derived (non-range), confirmed by user.
   - DQI must be Green/Yellow (score ≥55) to allow N_det.
   - Design selection follows biologist drivers (HVD/NTI/long t½), else baseline.
   - Reg-check rules now come from `reg_rules.yaml` (e.g., HVD → replicate warning).

2) **CV missing but CI+n present → CVfromCI path**
   - CV gate attempts CVfromCI when CI + n are available and assumptions are met.
   - Reg-check REG-003 fires if CI/n/assumptions are incomplete.
   - N_det still gated by CV confirmation + DQI.

3) **CV missing entirely → variability_rules → N_risk**
   - Variability layer supplies CV range + confidence.
   - DQI likely lower (missing CV evidence), prefer N_risk.
   - N_det remains disabled (no confirmed CV).

4) **FED/FASTED conflict in same source**
   - Extractor marks ambiguous values and adds `feeding_condition_conflict`.
   - Reg-check emits CLARIFY `FEEDING_CONDITION_CLARIFY`.
   - Ambiguous PK/CI are excluded from calculations but preserved in FullReport.

5) **Missing AUC/Cmax endpoints**
   - DQI hard red flag triggers: score=0, level=red, N_det blocked.
   - Pipeline still exports JSON/docx with warnings and Open Questions.

6) **study_condition = unknown**
   - Reg-check emits CLARIFY `REG-008` (OQ-160) for unknown fed/fasted state.

7) **design_testcases.json restored and expanded**
   - Restored Sergey scenario-based cases and appended synthetic edge cases.
   - Tests now validate intended logic without mirroring engine thresholds.
   - `golden_set.json` moved to `docs/_archive/golden_set.json.20260220-1843` to avoid mixing with design testcases.

5) **Conflicting sources**
   - Validator adds `conflict_detected` warnings.
   - Reg-check REG-007 fires (conflicting values), DQI consistency is penalized.

## D) Missing Deliverables / Risks

- `BIOLOGISTs_files/nsight Core Architecture.md` has no canonical target; not integrated.
- `reg_rules.yaml` now drives reg-checks; legacy “Decision 85” required PK rule is not present in the biologist ruleset (no YAML-driven rule for it).
- `cv.derived_from_ci.assumptions_confirmed_by_human` is referenced by biologist rules, but the current schema only has a single CV confirmation flag; it is mapped to the existing confirmation to avoid permanent CLARIFY.
- DQI red-flag overrides in the doc are not fully implemented as code-level overrides (current DQI uses weighted scoring with reasons).

# API

## POST /search_sources

Request:
```json
{"inn": "metformin", "retmax": 10}
```

Response: list of sources with PMID/PMCID and year evidence.

## POST /extract_pk

Request:
```json
{"inn": "metformin", "sources": ["12345678", "PMCID:PMC123456"]}
```

Response: PK values with NumericValue evidence, missing metrics, validation issues.

## POST /select_design

Request:
```json
{"pk_json": {...}, "cv_input": {...}, "nti": false}
```

Response: design and rule-based reasoning.

## POST /calc_sample_size

Request:
```json
{
  "design": "2x2 crossover",
  "cv_input": {"cv": {"value": 30, "unit": "%", "evidence": [...]}, "confirmed": true},
  "power": 0.8,
  "alpha": 0.05,
  "dropout": 0.1,
  "screen_fail": 0.1
}
```

Response: N_total, N_rand, N_screen with evidence.

## POST /variability_estimate

Rule-based CV range from BCS/logP/t1/2/first-pass/CYP/NTI.

## POST /risk_estimate

Monte Carlo probability of BE success with CV distribution.

## POST /reg_check

Returns checklist items with OK/RISK/CLARIFY.

## POST /build_docx

Builds synopsis docx via `docxtpl`.

# PK Extraction Specification

## 1. Scope

Extraction of pharmacokinetic (PK) and bioequivalence (BE) parameters
from PubMed/PMC publications into structured JSON with mandatory evidence binding.

Purpose:
- Reproducibility
- Regulatory transparency
- Downstream probabilistic modelling

---

## 2. Required Fields

### 2.1 Study Metadata

- pmid (string)
- title (string)
- population_type: healthy | patients
- fed_state: fasted | fed | mixed | not_reported
- dose_type: single | multiple
- design_type:
    - 2x2_crossover
    - replicate
    - parallel
- log_analysis: true | false
- n_total
- n_randomized
- n_completed
- n_per_sequence (optional)

---

### 2.2 PK Parameters (each with unit + evidence)

- Cmax_mean
- Cmax_sd
- Cmax_unit
- AUC0_t_mean
- AUC0_t_sd
- AUC_unit
- AUC0_inf_mean
- AUC0_inf_sd
- t_half_mean
- t_half_sd
- t_half_unit
- CVintra_Cmax (%)
- CVintra_AUC (%)

---

### 2.3 BE Parameters

- GMR_Cmax
- CI90_Cmax_low
- CI90_Cmax_high
- GMR_AUC
- CI90_AUC_low
- CI90_AUC_high

---

## 3. Evidence JSON Format

`json
{
  "parameter": "CVintra_Cmax",
  "value": 34,
  "unit": "%",
  "pmid": "12345678",
  "evidence_text": "The intra-subject CV for Cmax was 34%.",
  "location": "Table 2"
}ы
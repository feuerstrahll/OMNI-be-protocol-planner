# PK Extraction Specification

## 1. Scope

Extraction of pharmacokinetic (PK) and bioequivalence (BE) parameters
from PubMed/PMC publications into structured JSON with mandatory evidence binding.

Purpose:
- Reproducibility
- Regulatory transparency
- Downstream probabilistic modelling

---

## 2. JSON Schema (Full, Hierarchical)

The root object MUST contain a `study_arms` array. A single article can contain
separate data for FASTING and FED conditions; each must be represented as a
separate study arm to avoid overwriting.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "PK Extraction Output",
  "type": "object",
  "additionalProperties": false,
  "required": ["pmid", "title", "study_arms"],
  "properties": {
    "pmid": {"type": "string"},
    "title": {"type": "string"},
    "population_type": {"type": "string", "enum": ["healthy", "patients", "not_reported"]},
    "dose_type": {"type": "string", "enum": ["single", "multiple", "not_reported"]},
    "design_type": {
      "type": "string",
      "enum": ["2x2_crossover", "replicate", "parallel", "other", "not_reported"]
    },
    "log_analysis": {"type": ["boolean", "null"]},
    "n_total": {"type": ["integer", "null"], "minimum": 1},
    "n_randomized": {"type": ["integer", "null"], "minimum": 1},
    "n_completed": {"type": ["integer", "null"], "minimum": 1},
    "n_per_sequence": {"type": ["integer", "null"], "minimum": 1},
    "study_arms": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["arm_id", "condition", "dosage_form"],
        "properties": {
          "arm_id": {"type": "string"},
          "condition": {"type": "string", "enum": ["fasted", "fed", "mixed", "not_reported"]},
          "dosage_form": {"type": "string", "enum": ["IR", "MR", "ER"]},
          "n_arm": {"type": ["integer", "null"], "minimum": 1},
          "administration_notes": {"type": ["string", "null"]},
          "meal_details": {"type": ["string", "null"]},

          "Cmax_mean": {"type": ["number", "null"]},
          "Cmax_sd": {"type": ["number", "null"]},
          "Cmax_unit": {"type": ["string", "null"]},

          "AUC0_t_mean": {"type": ["number", "null"]},
          "AUC0_t_sd": {"type": ["number", "null"]},
          "AUC_unit": {"type": ["string", "null"]},

          "AUC0_inf_mean": {"type": ["number", "null"]},
          "AUC0_inf_sd": {"type": ["number", "null"]},

          "t_half_mean": {"type": ["number", "null"]},
          "t_half_sd": {"type": ["number", "null"]},
          "t_half_unit": {"type": ["string", "null"]},

          "CVintra_Cmax": {"type": ["number", "null"]},
          "CVintra_AUC": {"type": ["number", "null"]},

          "GMR_Cmax": {"type": ["number", "null"]},
          "CI90_Cmax_low": {"type": ["number", "null"]},
          "CI90_Cmax_high": {"type": ["number", "null"]},

          "GMR_AUC": {"type": ["number", "null"]},
          "CI90_AUC_low": {"type": ["number", "null"]},
          "CI90_AUC_high": {"type": ["number", "null"]}
        }
      }
    },

    "evidence": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["parameter", "value", "pmid", "evidence_text"],
        "properties": {
          "parameter": {"type": "string"},
          "value": {"type": ["number", "string"]},
          "unit": {"type": ["string", "null"]},
          "pmid": {"type": "string"},
          "evidence_text": {"type": "string"},
          "location": {"type": ["string", "null"]}
        }
      }
    }
  }
}
```

---

## 3. Evidence JSON Format

Each numeric value MUST include evidence with source and excerpt.

```json
{
  "parameter": "CVintra_Cmax",
  "value": 34,
  "unit": "%",
  "pmid": "12345678",
  "evidence_text": "The intra-subject CV for Cmax was 34%.",
  "location": "Table 2"
}
```

---

## 4. Negative Instructions (LLM Safety)

- If a value is provided as Mean ± SEM, mathematically convert it to SD.
- If the exact value of a parameter is missing in the text, strictly return null.
- Do not invent, hallucinate, or average values.

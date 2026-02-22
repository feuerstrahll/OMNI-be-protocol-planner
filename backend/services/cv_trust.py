"""Confidence scale and doubtful-source rules for CV trust policy (N_det without human confirmation).

Confidence scale (0..1), set in cv_gate:
  - manual CV (user input)           → 1.0
  - direct CVintra (regex in text)   → 0.9
  - CV derived from CI (assumptions) → 0.8
  - LLM CV from full text            → 0.65 (base; penalties apply)
  - range/heuristic                  → 0.4

Red flags: ambiguous_condition, multiple_values_in_source, conflict_detected:* → doubtful (forbid).
llm_extracted_requires_human_review → penalty (minus) only, not forbid.
"""

from __future__ import annotations

from backend.schemas import CVInfo

# Minimum confidence_score (0..1) to allow using CV for N_det without human confirmation
AUTO_CV_THRESHOLD = 0.85

# Warnings that forbid auto-use of CV for N_det (set confidence to 0 or treat as doubtful)
DOUBTFUL_FORBID = frozenset({"ambiguous_condition", "multiple_values_in_source"})
DOUBTFUL_PREFIX = "conflict_detected"

# Penalty applied to confidence_score when this warning is present (not a full forbid)
PENALTY_LLM_REVIEW = 0.15


def is_cv_doubtful(cv_info: CVInfo) -> bool:
    """True if CV source is doubtful and must not be used for N_det without human confirmation."""
    warnings = cv_info.warnings or []
    if any(w in DOUBTFUL_FORBID for w in warnings):
        return True
    if any(w.startswith(DOUBTFUL_PREFIX) for w in warnings):
        return True
    return False

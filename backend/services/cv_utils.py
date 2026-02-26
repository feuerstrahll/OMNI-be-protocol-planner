"""
CV (coefficient of variation) normalization utilities.
Internal standard: store and compare in PERCENT (e.g. 30.0 = 30%).
Rules in YAML may use either fraction (0.30) or percent (30).
"""
from __future__ import annotations


def cv_to_percent(cv: float | None) -> float | None:
    """Convert CV to percent. If value > 1, treat as percent; else as fraction."""
    if cv is None:
        return None
    try:
        v = float(cv)
        if v > 1.0:
            return v
        return v * 100.0
    except (TypeError, ValueError):
        return None


def cv_to_fraction(cv: float | None) -> float | None:
    """Convert CV to fraction [0,1]. If value > 1, treat as percent; else as fraction."""
    if cv is None:
        return None
    try:
        v = float(cv)
        if v > 1.0:
            return v / 100.0
        return v
    except (TypeError, ValueError):
        return None


def cv_meets_threshold(cv_value: float | None, threshold: float) -> bool:
    """
    Check if CV meets threshold. Threshold in percent if > 1, else fraction.
    cv_value: always in PERCENT (e.g. 30.0 = 30%).
    """
    if cv_value is None:
        return False
    try:
        cv = float(cv_value)
        if threshold <= 1.0:
            return (cv / 100.0) >= threshold
        return cv >= threshold
    except (TypeError, ValueError):
        return False

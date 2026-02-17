from __future__ import annotations

import math
from typing import List

import numpy as np

from backend.schemas import CVRange, NumericValue, RiskRequest, RiskResponse


def estimate_risk(data: RiskRequest) -> RiskResponse:
    cv_low = data.cv_range.low.value
    cv_high = data.cv_range.high.value
    cv_mode = data.cv_range.mode.value if data.cv_range.mode else (cv_low + cv_high) / 2

    if data.distribution == "triangular":
        samples = np.random.triangular(cv_low, cv_mode, cv_high, data.n_sim)
    else:
        mu, sigma = _lognormal_params_from_range(cv_low, cv_high, cv_mode)
        samples = np.random.lognormal(mean=mu, sigma=sigma, size=data.n_sim)
        samples = np.clip(samples, cv_low, cv_high)

    powers = [_tost_power(cv, data.N_total.value, data.alpha, data.theta1, data.theta2) for cv in samples]
    p_success = float(np.mean(powers))

    risk_level = "green" if p_success >= 0.8 else "yellow" if p_success >= 0.6 else "red"
    drivers = [
        f"CV range {cv_low:.1f}-{cv_high:.1f}% drives power variability",
        f"N_total={data.N_total.value:.0f} affects standard error",
    ]
    assumptions = [
        "Log-scale model with true ratio=1.0",
        "Approximate TOST power formula",
        f"Distribution={data.distribution}",
    ]

    evidence = [
        {
            "source_type": "URL",
            "source": "calc://risk_model",
            "snippet": "Monte Carlo over CV distribution using approximate TOST power",
            "context": f"n_sim={data.n_sim}",
        }
    ]

    return RiskResponse(
        p_success=NumericValue(value=p_success, unit="probability", evidence=evidence),
        risk_level=risk_level,
        drivers=drivers,
        assumptions=assumptions,
        warnings=[],
    )


def _tost_power(cv_percent: float, n_total: float, alpha: float, theta1: float, theta2: float) -> float:
    cv = cv_percent / 100.0
    # Log-scale sigma from CV.
    sigma = math.sqrt(max(1e-9, math.log(1 + cv * cv)))
    # Standard error for 2x2 crossover (within-subject).
    se = math.sqrt(2) * sigma / math.sqrt(max(2.0, n_total))
    z_alpha = _norm_ppf(1 - alpha)
    crit = math.log(theta2)
    # TOST power approximation under true ratio=1.0.
    margin = (crit / se) - z_alpha
    if margin <= 0:
        return 0.0
    return max(0.0, min(1.0, 2 * _norm_cdf(margin) - 1))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    # Acklam approximation
    if p <= 0 or p >= 1:
        raise ValueError("p must be in (0,1)")

    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]

    plow = 0.02425
    phigh = 1 - plow

    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    if phigh < p:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )

    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def _lognormal_params_from_range(low: float, high: float, mode: float) -> tuple[float, float]:
    low = max(low, 1e-3)
    high = max(high, low + 1e-3)
    mode = max(min(mode, high), low)
    # Approx: 95% interval -> 1.96 sigma on log scale
    sigma = (math.log(high) - math.log(low)) / (2 * 1.96)
    mu = math.log(mode)
    return mu, max(0.01, sigma)

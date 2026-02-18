from __future__ import annotations

import hashlib
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

from backend.schemas import CVInfo, SampleSizeRisk


def compute_sample_size_risk(
    inn: str,
    cv_info: CVInfo,
    alpha: float,
    power: float,
    n_sims: int,
    seed: Optional[int],
    distribution: Optional[str],
    cv_distribution_path: str = "docs/CV_DISTRIBUTION.md",
) -> Tuple[Optional[SampleSizeRisk], List[str]]:
    warnings: List[str] = []
    low, high, mode = cv_info.range_low, cv_info.range_high, cv_info.range_mode
    if low is None or high is None:
        return None, ["cv_range_missing"]
    if mode is None:
        mode = (low + high) / 2.0

    dist_name, dist_notes = _select_distribution(distribution, cv_distribution_path)
    warnings.extend(dist_notes)

    seed_val = seed if seed is not None else _derive_seed(inn, low, high, mode, alpha, power, n_sims, dist_name)
    rng = np.random.Generator(np.random.PCG64(seed_val))
    samples = _sample_cv(rng, dist_name, low, high, mode, n_sims, warnings)

    n_required = _required_n_array(samples, power, alpha)
    if n_required.size == 0:
        return None, ["cv_sampling_failed"]

    n_targets = {}
    p_success_at_n = {}
    for target in (0.7, 0.8, 0.9):
        n_at = int(np.quantile(n_required, target, method="higher"))
        n_targets[f"{target:.1f}"] = n_at
        p_success_at_n[f"{target:.1f}"] = float(np.mean(n_required <= n_at))

    sensitivity_notes = [
        f"Distribution={dist_name} low={low:.1f} mode={mode:.1f} high={high:.1f}",
        f"alpha={alpha}, power={power}, n_sims={n_sims}",
    ]
    if cv_info.range_confidence:
        sensitivity_notes.append(f"range_confidence={cv_info.range_confidence}")

    return (
        SampleSizeRisk(
            cv_distribution=dist_name,
            n_targets=n_targets,
            p_success_at_n=p_success_at_n,
            sensitivity_notes=sensitivity_notes,
            warnings=warnings,
            seed=seed_val,
            n_sims=n_sims,
            rng_name="PCG64",
            method="mc",
            numpy_version=np.__version__,
        ),
        warnings,
    )


def _select_distribution(distribution: Optional[str], path: str) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    if distribution:
        return distribution, warnings
    # Try load rules (placeholder for future structured content).
    if not os.path.exists(path):
        warnings.append("cv_distribution_default")
        return "triangular", warnings
    try:
        text = open(path, "r", encoding="utf-8").read().strip()
    except Exception:
        warnings.append("cv_distribution_default")
        return "triangular", warnings
    if not text or "TODO" in text.upper():
        warnings.append("cv_distribution_default")
        return "triangular", warnings
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict) and data.get("distribution"):
            return str(data["distribution"]), warnings
    except Exception:
        warnings.append("cv_distribution_default")
        return "triangular", warnings
    warnings.append("cv_distribution_default")
    return "triangular", warnings


def _sample_cv(
    rng: np.random.Generator,
    dist_name: str,
    low: float,
    high: float,
    mode: float,
    n_sims: int,
    warnings: List[str],
) -> np.ndarray:
    if dist_name == "triangular":
        return rng.triangular(low, mode, high, n_sims)
    if dist_name == "lognormal":
        mu, sigma = _lognormal_params_from_range(low, high, mode)
        samples = rng.lognormal(mean=mu, sigma=sigma, size=n_sims)
        return np.clip(samples, low, high)
    warnings.append("cv_distribution_unknown")
    return rng.triangular(low, mode, high, n_sims)


def _required_n_array(cv_values: np.ndarray, power: float, alpha: float) -> np.ndarray:
    cv = np.asarray(cv_values, dtype=float) / 100.0
    sigma = np.sqrt(np.log(1 + cv * cv))
    z_alpha = _inv_norm_cdf(1 - alpha)
    z_beta = _inv_norm_cdf(power)
    delta = math.log(1.25)
    n_total = np.ceil(((z_alpha + z_beta) * math.sqrt(2) * sigma / delta) ** 2)
    n_total = np.maximum(2, n_total)
    return n_total.astype(int)


def _derive_seed(
    inn: str,
    low: float,
    high: float,
    mode: float,
    alpha: float,
    power: float,
    n_sims: int,
    dist_name: str,
) -> int:
    payload = f"{inn}|{low:.4f}|{high:.4f}|{mode:.4f}|{alpha:.4f}|{power:.4f}|{n_sims}|{dist_name}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _lognormal_params_from_range(low: float, high: float, mode: float) -> Tuple[float, float]:
    low = max(low, 1e-3)
    high = max(high, low + 1e-3)
    mode = max(min(mode, high), low)
    sigma = (math.log(high) - math.log(low)) / (2 * 1.96)
    mu = math.log(mode)
    return mu, max(0.01, sigma)


def _inv_norm_cdf(p: float) -> float:
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

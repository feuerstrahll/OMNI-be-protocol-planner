from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from typing import Dict, Optional, Tuple

from backend.schemas import CVInput, NumericValue, SampleSizeResponse
from backend.services.utils import now_iso


def calc_sample_size(
    design: str,
    cv_input: CVInput,
    power: float,
    alpha: float,
    dropout: float,
    screen_fail: float,
) -> SampleSizeResponse:
    if not cv_input.confirmed:
        return SampleSizeResponse(
            N_total=None,
            N_rand=None,
            N_screen=None,
            warnings=["CVintra must be confirmed before sample size calculation."],
            details={},
        )

    cv = cv_input.cv.value
    details: Dict[str, str] = {
        "design": design,
        "timestamp": now_iso(),
    }

    r_result = _try_powertost(design, cv, power, alpha)
    if r_result:
        n_total, details_text = r_result
        details["engine"] = "PowerTOST"
        details["raw"] = details_text
        evidence = [
            {
                "source_type": "URL",
                "source": "calc://powertost",
                "snippet": details_text,
                "context": "PowerTOST via Rscript",
            }
        ]
        return _build_response(n_total, dropout, screen_fail, evidence, details)

    # fallback approximate formula
    n_total, formula_text = _approximate_n_total(cv, power, alpha)
    warnings = [
        "Rscript/PowerTOST not available. Used approximate formula for N."
    ]
    evidence = [
        {
            "source_type": "URL",
            "source": "calc://approx",
            "snippet": formula_text,
            "context": "Approximate log-scale TOST formula",
        }
    ]
    details["engine"] = "approx"
    details["raw"] = formula_text
    return _build_response(n_total, dropout, screen_fail, evidence, details, warnings)


def _build_response(
    n_total: int,
    dropout: float,
    screen_fail: float,
    evidence: list,
    details: Dict[str, str],
    warnings: Optional[list] = None,
) -> SampleSizeResponse:
    n_rand = math.ceil(n_total / max(1e-6, (1 - dropout)))
    n_screen = math.ceil(n_rand / max(1e-6, (1 - screen_fail)))

    return SampleSizeResponse(
        N_total=NumericValue(value=float(n_total), unit="subjects", evidence=evidence),
        N_rand=NumericValue(
            value=float(n_rand),
            unit="subjects",
            evidence=[
                {
                    "source_type": "URL",
                    "source": "calc://adjustments",
                    "snippet": f"N_rand = ceil(N_total/(1-dropout)) with dropout={dropout}",
                    "context": "Dropout adjustment",
                }
            ],
        ),
        N_screen=NumericValue(
            value=float(n_screen),
            unit="subjects",
            evidence=[
                {
                    "source_type": "URL",
                    "source": "calc://adjustments",
                    "snippet": f"N_screen = ceil(N_rand/(1-screen_fail)) with screen_fail={screen_fail}",
                    "context": "Screen-fail adjustment",
                }
            ],
        ),
        details=details,
        warnings=warnings or [],
    )


def _try_powertost(design: str, cv: float, power: float, alpha: float) -> Optional[Tuple[int, str]]:
    rscript = os.getenv("RSCRIPT_PATH") or shutil.which("Rscript") or shutil.which("Rscript.exe")
    if not rscript:
        return None

    cmd = [
        rscript,
        "r/powertost_runner.R",
        "--design",
        design,
        "--cv",
        str(cv),
        "--power",
        str(power),
        "--alpha",
        str(alpha),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except Exception:
        return None

    try:
        data = json.loads(result.stdout.strip())
        n_total = int(data.get("N_total"))
        return n_total, result.stdout.strip()
    except Exception:
        return None


def _approximate_n_total(cv_percent: float, power: float, alpha: float) -> Tuple[int, str]:
    cv = cv_percent / 100.0
    # Convert CV to log-scale sigma for within-subject variability.
    sigma = math.sqrt(math.log(1 + cv * cv))
    # z-scores for alpha and target power.
    z_alpha = _inv_norm_cdf(1 - alpha)
    z_beta = _inv_norm_cdf(power)
    # Equivalence margin on log-scale (theta2=1.25).
    delta = math.log(1.25)
    # Approximate total N for 2x2 crossover using TOST formula.
    n_total = math.ceil(((z_alpha + z_beta) * math.sqrt(2) * sigma / delta) ** 2)
    formula = (
        "n = ((z_(1-alpha) + z_power) * sqrt(2) * sigma / log(theta2))^2; "
        f"sigma=sqrt(log(1+CV^2)), CV={cv_percent}%"
    )
    return max(2, n_total), formula


# Inverse normal CDF approximation (Acklam)
# Adapted for internal use to avoid scipy dependency.

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

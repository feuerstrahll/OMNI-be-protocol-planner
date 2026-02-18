from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import List, Tuple


def _get_rscript_path() -> str | None:
    return os.getenv("RSCRIPT_PATH") or shutil.which("Rscript") or shutil.which("Rscript.exe")


def check_rscript() -> Tuple[bool, str]:
    rscript = _get_rscript_path()
    if not rscript:
        return False, "Rscript not found"
    try:
        result = subprocess.run([rscript, "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return False, "Rscript --version failed"
    except Exception as exc:
        return False, f"Rscript check failed: {exc}"
    return True, "Rscript available"


def check_powertost() -> Tuple[bool, str]:
    rscript = _get_rscript_path()
    if not rscript:
        return False, "Rscript not found"
    try:
        result = subprocess.run(
            [rscript, "-e", "suppressMessages(library(PowerTOST)); cat('OK')"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0 or "OK" not in (result.stdout or ""):
            return False, "PowerTOST not available"
    except Exception as exc:
        return False, f"PowerTOST check failed: {exc}"
    return True, "PowerTOST available"


def health() -> dict:
    rscript_ok, rscript_msg = check_rscript()
    powertost_ok, powertost_msg = check_powertost()
    msg = rscript_msg if rscript_ok else rscript_msg
    if rscript_ok:
        msg = powertost_msg
    return {
        "rscript_ok": rscript_ok,
        "powertost_ok": powertost_ok,
        "message": msg,
    }


def run_cvfromci(lower: float, upper: float, n: int, design: str = "2x2") -> Tuple[float | None, List[str]]:
    warnings: List[str] = []
    rscript = _get_rscript_path()
    if not rscript:
        return None, ["rscript_not_found"]

    script_path = os.path.join(os.path.dirname(__file__), "powertost_runner.R")
    if not os.path.exists(script_path):
        return None, ["powertost_runner_missing"]

    cmd = [
        rscript,
        script_path,
        "--lower",
        str(lower),
        "--upper",
        str(upper),
        "--n",
        str(n),
        "--design",
        design,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None, ["powertost_runner_failed"]

    if result.returncode != 0:
        return None, ["powertost_runner_failed"]

    try:
        payload = json.loads(result.stdout.strip())
    except Exception:
        return None, ["powertost_runner_invalid_json"]

    warnings.extend(payload.get("warnings", []) or [])
    cv_val = payload.get("cv", None)
    if cv_val is None:
        return None, warnings
    try:
        return float(cv_val), warnings
    except Exception:
        return None, ["powertost_runner_invalid_cv"]

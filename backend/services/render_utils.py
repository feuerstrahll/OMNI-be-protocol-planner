from __future__ import annotations

from typing import Any, Iterable, List

DEFAULT_PLACEHOLDER = "—"


def safe_str(x: Any, default: str = DEFAULT_PLACEHOLDER) -> str:
    if x is None:
        return default
    if isinstance(x, str):
        value = x.strip()
        return value if value else default
    return str(x)


def safe_num(x: Any, default: str = DEFAULT_PLACEHOLDER, ndigits: int | None = None) -> str:
    try:
        val = float(x)
    except (TypeError, ValueError):
        return default
    if ndigits is not None:
        fmt = f"{{:.{ndigits}f}}"
        return fmt.format(val)
    if val.is_integer():
        return str(int(val))
    return str(val)


def safe_pct(x: Any, default: str = DEFAULT_PLACEHOLDER) -> str:
    try:
        val = float(x)
    except (TypeError, ValueError):
        return default
    if 0 < val <= 1:
        val *= 100
    return f"{val:.1f}%"


def safe_list(xs: Iterable[Any] | None, default: List[Any] | None = None) -> List[Any]:
    if default is None:
        default = [DEFAULT_PLACEHOLDER]
    if xs is None:
        return list(default)
    xs_list = list(xs)
    return xs_list if xs_list else list(default)


def safe_join(xs: Iterable[Any] | None, sep: str = ", ", default: str = DEFAULT_PLACEHOLDER) -> str:
    if xs is None:
        return default
    items = [safe_str(x, default="") for x in xs if safe_str(x, default="")]
    if not items:
        return default
    return sep.join(items)


def safe_table(rows: List[dict] | None, default: List[dict] | None = None) -> List[dict]:
    if default is None:
        default = [{"col": DEFAULT_PLACEHOLDER}]
    if not rows:
        return list(default)
    return rows

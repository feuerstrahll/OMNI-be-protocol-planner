#!/usr/bin/env python
"""
Проверка скрапинга (PMC), парсинга и опционально LLM.

Использование:
  # из корня проекта (где backend как пакет):
  python -m backend.scripts.test_pmc_and_llm [PMCID]

  # только скрапинг + парсинг (без API ключа):
  python -m backend.scripts.test_pmc_and_llm PMC1234567

  # скрапинг + парсинг + извлечение через Yandex LLM (нужны YANDEX_API_KEY, YANDEX_FOLDER_ID):
  python -m backend.scripts.test_pmc_and_llm PMC1234567 --llm

Юнит-тесты (мок, без сети):
  pytest backend/tests/test_pmc_fetcher.py -v
"""
from __future__ import annotations

import argparse
import os
import re
import sys

from dotenv import load_dotenv
load_dotenv()  # эта функция найдет .env в корне и загрузит ключи в os.environ
# ===============================

# чтобы импорт backend работал из корня репо
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _prioritize_snippet_blocks(snippets_text: str, max_chars: int = 12000) -> str:
    blocks = [b.strip() for b in snippets_text.split("\n---\n") if b.strip()]

    def score(block: str) -> int:
        b = block.lower()
        s = 0
        if re.search(r"\bwithin[- ]subject\b|cv\s*[_w]*\s*%|%\s*cv|\bcv\b", b):
            s += 5
        if re.search(r"\b90\s*%?\s*ci\b|\bci\b|\bgmr\b|geometric mean ratio", b):
            s += 3
        if re.search(r"\bcmax\b|\bauc\b|tmax|t1/2|half[- ]life", b):
            s += 1
        if "location:" in b and any(k in b for k in ["results", "methods", "statistic", "pharmacokinetic"]):
            s += 2
        if "location:" in b and any(k in b for k in ["introduction", "discussion"]):
            s -= 1
        return s

    blocks.sort(key=score, reverse=True)
    out = []
    total = 0
    for b in blocks:
        add = b + "\n---\n"
        if total + len(add) > max_chars:
            break
        out.append(b)
        total += len(add)
    return "\n---\n".join(out)[:max_chars]


# CV as number after CI range in a table row (no % in cell; % is in header "Intra-subject CV (%)")
# Group 1=param, 2=CI_low, 3=CI_high, 4=CV value
_TABLE_ROW_WITH_CI_AND_CV = re.compile(
    r"(?m)^(Cmax|AUC0[-–]t|AUC0[-–](?:inf|∞)|AUC)\s+.*?"
    r"(\d+(?:\.\d+)?)\s*(?:–|-|to|,|;)\s*(\d+(?:\.\d+)?)"  # CI low/high
    r"\s+(\d+(?:\.\d+)?)",  # CV (no %)
    re.I,
)
# Header check for 90% CI (informational only)
_CI_HEADER = re.compile(r"90\s*%?\s*ci", re.I)


def _match_with_context(text: str, pattern: re.Pattern, context_chars: int = 250) -> tuple[str, str] | None:
    """Return (matched_text, context_around) or None."""
    m = pattern.search(text)
    if not m:
        return None
    start = max(0, m.start() - context_chars)
    end = min(len(text), m.end() + context_chars)
    return m.group(0), text[start:end]


def _extract_cv_regex_fallback(text: str) -> float | None:
    """Extract first CV value from table row (param + CI low-high + CV number)."""
    row_m = _TABLE_ROW_WITH_CI_AND_CV.search(text)
    if not row_m:
        return None
    try:
        val = float(row_m.group(4))
        return val if 1 <= val <= 150 else None
    except (ValueError, IndexError):
        return None


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged: list[tuple[int, int]] = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _extract_cv_ci_windows(full_text: str, window: int = 800) -> str:
    """Extract ±window char spans around table rows with CI and CV; merge overlapping."""
    if not full_text.strip():
        return ""
    intervals: list[tuple[int, int]] = []
    for m in _TABLE_ROW_WITH_CI_AND_CV.finditer(full_text):
        start = max(0, m.start() - window)
        end = min(len(full_text), m.end() + window)
        intervals.append((start, end))
    merged = _merge_intervals(intervals)
    chunks = [full_text[s:e].strip() for s, e in merged if full_text[s:e].strip()]
    return "\n---\n".join(chunks) if chunks else ""


def _contexts_for_llm(data: dict) -> list[tuple[str, str]]:
    snippets = (data.get("snippets_text") or "").strip()
    target = (data.get("target_text") or "").strip()
    full = (data.get("full_text") or "").strip()
    if snippets:
        snippets = _prioritize_snippet_blocks(snippets, max_chars=12000)
    full_windows = _extract_cv_ci_windows(full, window=800)
    if not full_windows.strip():
        full_windows = full[:12000]
    return [
        ("sec:snippets", snippets[:12000]),
        ("sec:results_pk_stats", target[:12000]),
        ("full_text", full_windows[:12000]),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Test PMC fetch, parse, and optional LLM extraction")
    parser.add_argument("pmcid", nargs="?", default="PMC6386472", help="PMC ID (e.g. PMC6386472)")
    parser.add_argument("--llm", action="store_true", help="Run Yandex LLM extract_pk_from_text on snippets")
    args = parser.parse_args()

    pmcid = args.pmcid.strip()
    if not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"

    from backend.services.pmc_fetcher import fetch_pmc_sections

    print("1) Скрапинг + парсинг PMC...")
    data = fetch_pmc_sections(pmcid)
    if not data:
        print("   Ошибка: пустой ответ")
        return
    print(f"   Ключи: {list(data.keys())}")
    print(f"   snippets_text: {len(data.get('snippets_text') or '')} символов")
    print(f"   target_text:   {len(data.get('target_text') or '')} символов")
    print(f"   full_text:     {len(data.get('full_text') or '')} символов")
    print(f"   supplementary_present: {data.get('supplementary_present')}")

    if (data.get("snippets_text") or "").strip():
        print("\n   Первый сниппет (начало):")
        print((data["snippets_text"].split("\n---\n")[0])[:500] + "...")

    if not args.llm:
        print("\n2) LLM не запускался (используйте --llm для проверки извлечения)")
        return

    if not os.getenv("YANDEX_API_KEY") or not os.getenv("YANDEX_FOLDER_ID"):
        print("\n2) LLM пропущен: задайте YANDEX_API_KEY и YANDEX_FOLDER_ID в .env")
        return

    print("\n2) Yandex LLM (extract_pk_from_text) по контекстам: snippets → target → full_text")
    from backend.services.yandex_llm import YandexLLMClient
    from backend.services.pk_extractor import _normalize_llm_pk_response

    client = YandexLLMClient()
    source_id = f"PMCID:{pmcid.replace('PMC', '').strip()}" if pmcid else "PMCID:unknown"
    contexts = _contexts_for_llm(data)
    result = None
    final_result = None
    cv_expected = False
    ci_expected = False
    cv_found = False
    ci_found = False
    for label, text in contexts:
        if not text.strip():
            continue
        print(f"\n   Контекст: {label}, len={len(text)}")
        has_ci_header = bool(_CI_HEADER.search(text))
        row_m = _TABLE_ROW_WITH_CI_AND_CV.search(text)
        has_cv_numeric = row_m is not None
        has_ci_numeric = row_m is not None
        if has_cv_numeric:
            cv_expected = True
        if has_ci_numeric:
            ci_expected = True
        try:
            ci_low = float(row_m.group(2)) if row_m else None
            ci_high = float(row_m.group(3)) if row_m else None
            cv_value = float(row_m.group(4)) if row_m else None
        except Exception:
            ci_low = ci_high = cv_value = None
        if row_m:
            print("   table row:", row_m.group(0))
            print("   extracted: ci_low=%s, ci_high=%s, cv=%s" % (ci_low, ci_high, cv_value))
        else:
            print("   table row: (none)")
            print("   has 90% CI header:", has_ci_header)
        result = client.extract_pk_from_text(
            text, inn="", source_id=source_id, location=label
        )
        print(f"   Результат: {result}")
        flat = _normalize_llm_pk_response(result or {})
        if flat.get("CI_low") is not None and flat.get("CI_high") is not None:
            ci_found = True
        if has_cv_numeric:
            if flat.get("CVintra") is None:
                cv_fallback = _extract_cv_regex_fallback(text)
                if cv_fallback is not None:
                    result = result if isinstance(result, dict) else {}
                    result.setdefault("pk_values", []).append(
                        {
                            "name": "CVintra",
                            "value": cv_fallback,
                            "unit": "%",
                            "evidence": [
                                {
                                    "excerpt": "regex fallback from table row",
                                    "pmid_or_url": source_id,
                                    "location": label,
                                }
                            ],
                        }
                    )
                    flat = _normalize_llm_pk_response(result or {})
                    print("   CV from regex fallback (LLM returned empty/wrong format)")
                else:
                    raise AssertionError(
                        f"Context {label} has CV numeric pattern but LLM returned no CVintra and regex fallback found nothing. result={result}"
                    )
            cv_found = cv_found or flat.get("CVintra") is not None
        if flat.get("CVintra") is not None:
            cv_found = True
        if cv_found and flat.get("CI_low") is not None and flat.get("CI_high") is not None:
            ci_found = True
        if cv_found:
            final_result = result
            break
        if isinstance(result, dict) and (result.get("pk_values") or result.get("ci_values")):
            final_result = result
    if cv_expected and not cv_found:
        raise AssertionError("Found CV in table/text, but neither LLM nor fallback produced CVintra.")
    if ci_expected and not ci_found:
        print("WARNING: Found CI bounds in table/text, but LLM did not return CI (expected; CI extraction not enforced in prompt).")
    if final_result:
        for k in ("pk_values", "ci_values"):
            if final_result.get(k):
                print(f"   {k}: {final_result[k]}")


if __name__ == "__main__":
    main()

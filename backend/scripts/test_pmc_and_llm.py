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


_CV_NUMERIC_PATTERN = re.compile(
    r"(within[- ]subject|cvw|cv_w|swr|\bcv\b)\s*[^0-9]{0,30}\d+(\.\d+)?\s*%"
    r"|"
    r"\d+(\.\d+)?\s*%\s*[^0-9]{0,30}(within[- ]subject|cvw|cv_w|swr|\bcv\b)",
    re.I,
)
_CI_NUMERIC_PATTERN = re.compile(
    r"90\s*%?\s*ci[^0-9]{0,30}\d+(\.\d+)?\s*(–|-|to)\s*\d+(\.\d+)?",
    re.I,
)


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
    """Extract ±window char spans around CV numeric and 90% CI numeric matches; merge overlapping."""
    if not full_text.strip():
        return ""
    intervals: list[tuple[int, int]] = []
    for pat in (_CV_NUMERIC_PATTERN, _CI_NUMERIC_PATTERN):
        for m in pat.finditer(full_text):
            start = max(0, m.start() - window)
            end = min(len(full_text), m.end() + window)
            intervals.append((start, end))
    merged = _merge_intervals(intervals)
    chunks = [full_text[s:e].strip() for s, e in merged if full_text[s:e].strip()]
    return "\n---\n".join(chunks) if chunks else ""


def _contexts_for_llm(data: dict, pmcid: str) -> list[tuple[str, str]]:
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

    client = YandexLLMClient()
    source_id = f"PMCID:{pmcid.replace('PMC', '').strip()}" if pmcid else "PMCID:unknown"
    contexts = _contexts_for_llm(data, pmcid)
    result = None
    for label, text in contexts:
        if not text.strip():
            continue
        print(f"\n   Контекст: {label}, len={len(text)}")
        has_cv_numeric = bool(_CV_NUMERIC_PATTERN.search(text))
        has_ci_numeric = bool(_CI_NUMERIC_PATTERN.search(text))
        print("   has CV numeric (number + % near CV/within-subject/CVw/Swr)?", has_cv_numeric)
        print("   has 90% CI with bounds (e.g. 80.0–125.0)?", has_ci_numeric)
        result = client.extract_pk_from_text(
            text, inn="", source_id=source_id, location=label
        )
        print(f"   Результат: {result}")
        if has_cv_numeric and result:
            cv_val = result.get("CVintra")
            if cv_val is None:
                raise AssertionError(
                    f"Context {label} has CV numeric pattern but LLM returned no CVintra. result={result}"
                )
        if has_ci_numeric and result:
            ci_low, ci_high = result.get("CI_low"), result.get("CI_high")
            if ci_low is None or ci_high is None:
                raise AssertionError(
                    f"Context {label} has 90% CI numeric pattern but LLM returned no CI_low/CI_high. result={result}"
                )
        if result and (result.get("CVintra") is not None or result.get("CI_low") is not None):
            break
    if result:
        for k in ("pk_values", "ci_values"):
            if result.get(k):
                print(f"   {k}: {result[k]}")


if __name__ == "__main__":
    main()

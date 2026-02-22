from __future__ import annotations

import re
import time
from typing import Dict, List, Tuple
from xml.etree import ElementTree

import requests

_TABLE_ROW_WITH_CI_AND_CV = re.compile(
    r"(?m)^(Cmax|AUC0[-–]t|AUC0[-–](?:inf|∞)|AUC)\s+.*?"
    r"(\d+(?:\.\d+)?)\s*(?:–|-|to|,|;)\s*(\d+(?:\.\d+)?)"
    r"\s+(\d+(?:\.\d+)?)",
    re.I,
)


def fetch_pmc_sections(pmcid: str) -> Dict[str, object]:
    """Fetch PMC XML and return structured text for LLM escalation strategy.

    Fetches the article and looks for triggers in all sections except References/Appendix
    and except supplement sections (sec-type/title containing "supplement").
    Supplement sections are not included in full_text, target_text, or snippets_text,
    so when supplementary_present is True, callers should surface the warning
    "data_may_be_in_supplementary" — it is returned in the "warnings" list for that reason.

    Returns dict with:
      - snippets_text: concatenated snippet text (or "")
      - target_text: joined Results/Pharmacokinetics/Statistical sections and tables
      - full_text: all collected body text (sections + tables; no supplement content)
      - supplementary_present: bool — True if article has supplementary-material
      - warnings: list of str — includes "data_may_be_in_supplementary" when supplementary_present
    """
    numeric_id = _normalize_pmcid(pmcid)
    if not numeric_id:
        return {"snippets_text": "", "target_text": "", "full_text": "", "supplementary_present": False, "warnings": []}

    # eFetch for db=pmc; PMC may update E-utilities (e.g. Feb 2026) — eFetch expected to remain.
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pmc", "id": numeric_id, "rettype": "xml", "retmode": "xml"}
    try:
        resp = requests.get(url, params=params, timeout=20)
        time.sleep(0.35)
        if resp.status_code != 200:
            return {"snippets_text": "", "target_text": "", "full_text": "", "supplementary_present": False, "warnings": []}
        root = ElementTree.fromstring(resp.content)
    except Exception:
        return {"snippets_text": "", "target_text": "", "full_text": "", "supplementary_present": False, "warnings": []}

    supplementary_present = bool(
        root.findall(".//supplementary-material")
        or root.findall(".//sec[@sec-type='supplementary-material']")
    )
    body = root.find(".//body")
    if body is None:
        return {
            "snippets_text": "", "target_text": "", "full_text": "", "supplementary_present": supplementary_present,
            "warnings": ["data_may_be_in_supplementary"] if supplementary_present else [],
        }

    parent_map = _build_parent_map(root)

    sec_texts: List[Dict[str, str]] = []

    def _collect_sec(sec, depth_label: str = "") -> None:
        title = (sec.findtext("title") or "").strip()
        sec_type = (sec.attrib.get("sec-type") or "").lower()
        if _is_excluded_sec(sec_type, title):
            return
        text_chunks: List[str] = []
        for p in sec.findall("./p"):
            if _is_ref_like(p, parent_map) or _is_inside_table(p, parent_map):
                continue
            full_text = " ".join("".join(p.itertext()).split())
            if full_text:
                text_chunks.append(full_text)
        label = title or sec_type or depth_label or "section"
        if text_chunks:
            sec_texts.append({"title": label, "text": "\n".join(text_chunks)})
        for child in sec.findall("./sec"):
            _collect_sec(child, label)

    for top_sec in body.findall("./sec"):
        _collect_sec(top_sec)

    # Fallback: body paragraphs (excluding refs/tables) if no sections
    if not sec_texts:
        body_paras = []
        for p in body.findall(".//p"):
            if _is_ref_like(p, parent_map) or _is_inside_table(p, parent_map):
                continue
            full_text = " ".join("".join(p.itertext()).split())
            if full_text:
                body_paras.append(full_text)
        if body_paras:
            sec_texts.append({"title": "body", "text": "\n".join(body_paras)})

    table_docs: List[Dict[str, str]] = []
    for tw in root.findall(".//table-wrap"):
        label = (tw.findtext("label") or "").strip()
        caption = (tw.findtext("caption/title") or "").strip() or (tw.findtext("caption/p") or "").strip()
        grid_text, header = _table_grid_text(tw)
        foot_parts = []
        for foot in tw.findall(".//table-wrap-foot//p"):
            ft = " ".join("".join(foot.itertext()).split())
            if ft:
                foot_parts.append(ft)
        foot = "\n".join(foot_parts) if foot_parts else ""
        parts = [part for part in [label, caption, grid_text, foot] if part]
        if parts:
            table_docs.append(
                {
                    "label": label or "table",
                    "sec_label": _nearest_sec_label(tw, parent_map),
                    "as_text": "\n".join(parts),
                    "caption": caption,
                    "header": header,
                    "foot": foot,
                }
            )

    snippets = build_snippets(sec_texts, table_docs, source_id=pmcid)
    snippets_text = (
        "\n---\n".join(
            f"LOCATION: {sn['location']}\nSOURCE: {sn.get('source_id','')}\nTEXT:\n{sn['text']}"
            for sn in snippets
        )
        if snippets
        else ""
    )

    target_chunks = []
    for sec in sec_texts:
        title_l = (sec.get("title") or "").lower()
        if any(k in title_l for k in ["result", "pharmacokinetic", "statistic"]):
            target_chunks.append(sec["text"])
    for table in table_docs:
        target_chunks.append(table["as_text"])
    target_text = "\n\n".join(target_chunks)

    full_text = "\n\n".join([s["text"] for s in sec_texts] + [t["as_text"] for t in table_docs])

    warnings: List[str] = []
    if supplementary_present:
        warnings.append("data_may_be_in_supplementary")

    return {
        "snippets_text": snippets_text,
        "target_text": target_text,
        "full_text": full_text,
        "supplementary_present": supplementary_present,
        "warnings": warnings,
    }


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


def prepare_pmc_llm_contexts(pmc_payload: dict, max_chars: int = 12000) -> List[Tuple[str, str]]:
    snippets = (pmc_payload.get("snippets_text") or "").strip()
    target = (pmc_payload.get("target_text") or "").strip()
    full = (pmc_payload.get("full_text") or "").strip()
    contexts: List[Tuple[str, str]] = []

    if snippets:
        snippets = _prioritize_snippet_blocks(snippets, max_chars=max_chars)
        if snippets:
            contexts.append(("sec:snippets", snippets[:max_chars]))

    if target:
        contexts.append(("sec:results_pk_stats", target[:max_chars]))

    if full:
        intervals: List[Tuple[int, int]] = []
        window = 800
        for m in _TABLE_ROW_WITH_CI_AND_CV.finditer(full):
            start = max(0, m.start() - window)
            end = min(len(full), m.end() + window)
            intervals.append((start, end))
        merged = _merge_intervals(intervals)
        chunks = [full[s:e].strip() for s, e in merged if full[s:e].strip()]
        full_windows = "\n---\n".join(chunks) if chunks else full[:max_chars]
        if full_windows:
            contexts.append(("full_text", full_windows[:max_chars]))

    return contexts


def _merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping (start, end) intervals. Input can be list of lists or tuples."""
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged: List[Tuple[int, int]] = []
    for start, end in sorted_intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def build_snippets(sections: List[Dict[str, str]], tables: List[Dict[str, str]], source_id: str = "") -> List[Dict]:
    """Return up to 20 deduped snippets around PK/CV triggers with locations.
    Overlapping windows (e.g. multiple triggers in one paragraph) are merged into one snippet per span.
    """
    triggers = [
        (
            r"\b(?:CV|CVintra|CVw|CV_w|Swr|within[- ]subject|intra[- ]subject|intrasubject|"
            r"within[- ]subject[- ]standard[- ]deviation)\b|%\s*CV\b|CV\s*%",
            "cv",
        ),
        (r"\b(?:CI|90 percent|CI 90|confidence interval|GMR|geometric mean ratio|bioequivalen(?:ce|t)?|RSABE|reference[- ]scaled)\b|90%\s*CI", "ci"),
        (r"\b(?:Cmax|AUC|AUC0[-–]t|AUC0[-–]inf|Tmax|t1/2|half[- ]life)\b", "pk"),
    ]
    window = 400  # chars on each side (~800 total)
    snippets: List[Dict] = []

    def _intervals_for_text(text: str) -> List[Tuple[int, int]]:
        intervals: List[Tuple[int, int]] = []
        for pattern, _ in triggers:
            for m in re.finditer(pattern, text, flags=re.IGNORECASE):
                start = max(0, m.start() - window)
                end = min(len(text), m.end() + window)
                intervals.append((start, end))
        return _merge_intervals(intervals)

    for sec in sections:
        text = sec.get("text", "")
        title = sec.get("title") or "section"
        loc = f"sec:{title}"
        for start, end in _intervals_for_text(text):
            chunk = text[start:end].strip()
            if chunk:
                snippets.append({"text": chunk, "location": loc, "source_id": source_id})

    for table in tables:
        text = table.get("as_text", "")
        label = table.get("label") or "table"
        sec_label = table.get("sec_label") or ""
        loc = f"table:{label}" if label else "table"
        if sec_label:
            loc = f"{loc} ({sec_label})"
        for start, end in _intervals_for_text(text):
            chunk = text[start:end].strip()
            if chunk:
                snippets.append({"text": chunk, "location": loc, "source_id": source_id})

    # Deduplicate by normalized text, limit to 20
    uniq: List[Dict] = []
    seen = set()
    for sn in snippets:
        key = " ".join(sn["text"].split()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(sn)
        if len(uniq) >= 20:
            break

    return uniq


def _normalize_pmcid(pmcid: str) -> str:
    if not pmcid:
        return ""
    match = re.search(r"(\d+)", pmcid)
    return match.group(1) if match else ""


def _is_excluded_sec(sec_type: str, title: str) -> bool:
    t = (sec_type or "").lower() + " " + (title or "").lower()
    return any(word in t for word in ["reference", "acknowledg", "appendix", "supplement"])


def _is_ref_like(elem, parent_map: Dict) -> bool:
    cur = elem
    while cur is not None:
        tag = cur.tag.lower()
        if "ref-list" in tag or tag == "reference":
            return True
        cur = parent_map.get(cur)
    return False


def _is_inside_table(elem, parent_map: Dict) -> bool:
    cur = elem
    while cur is not None:
        tag = cur.tag.lower()
        if "table-wrap" in tag or tag == "table":
            return True
        cur = parent_map.get(cur)
    return False


def _row_text(row) -> str:
    cells = []
    for cell in row.findall(".//th") + row.findall(".//td"):
        text = " ".join("".join(cell.itertext()).split())
        if text:
            cells.append(text)
    return "\t".join(cells)


def _table_grid_text(tw) -> Tuple[str, str]:
    rows = tw.findall(".//tr")
    if not rows:
        return "", ""
    header = _row_text(rows[0])
    data_rows = [_row_text(r) for r in rows[1:]] if len(rows) > 1 else []
    grid_lines = []
    if header:
        grid_lines.append(header)
    for row in data_rows:
        if row:
            grid_lines.append(row)
    grid_text = "\n".join(grid_lines)
    return grid_text, header


def _build_parent_map(root) -> Dict:
    parent_map = {}
    for parent in root.iter():
        for child in parent:
            parent_map[child] = parent
    return parent_map


def _nearest_sec_label(elem, parent_map: Dict) -> str:
    cur = elem
    while cur is not None:
        if cur.tag.lower().endswith("sec"):
            title = cur.findtext("title") or ""
            label = cur.findtext("label") or ""
            return (label or title).strip()
        cur = parent_map.get(cur)
    return ""

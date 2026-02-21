from __future__ import annotations

import re
import time
from typing import List
from xml.etree import ElementTree

import requests


def fetch_pmc_sections(pmcid: str, sections: List[str] | None = None) -> str:
    sections = sections or ["methods", "results"]
    try:
        numeric_id = _normalize_pmcid(pmcid)
        if not numeric_id:
            return ""
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        params = {"db": "pmc", "id": numeric_id, "rettype": "xml", "retmode": "xml"}
        resp = requests.get(url, params=params, timeout=20)
        time.sleep(0.35)
        if resp.status_code != 200:
            return ""
        root = ElementTree.fromstring(resp.text)
        section_words = [s.lower() for s in sections]
        texts: List[str] = []

        for sec in root.findall(".//sec"):
            sec_type = (sec.attrib.get("sec-type") or "").lower()
            title = (sec.findtext("title") or "").lower()
            if not _contains_any(sec_type, section_words) and not _contains_any(title, section_words):
                continue
            for p in sec.findall(".//p"):
                full_text = "".join(p.itertext()).strip()
                if full_text:
                    texts.append(full_text)

        if not texts:
            for p in root.findall(".//p"):
                full_text = "".join(p.itertext()).strip()
                if full_text:
                    texts.append(full_text)

        for table in root.findall(".//table-wrap"):
            rows = table.findall(".//tr")
            if not rows:
                continue
            header_row = _row_text(rows[0])
            data_rows = [_row_text(r) for r in rows[1:]] if len(rows) > 1 else []
            formatted = "TABLE: " + header_row
            for row in data_rows:
                if row:
                    formatted += f" | {row}"
            texts.append(formatted)

        return "\n".join([t for t in texts if t])
    except Exception:
        return ""


def _normalize_pmcid(pmcid: str) -> str:
    if not pmcid:
        return ""
    match = re.search(r"(\d+)", pmcid)
    return match.group(1) if match else ""


def _contains_any(text: str, tokens: List[str]) -> bool:
    for token in tokens:
        if token in text:
            return True
    return False


def _row_text(row) -> str:
    cells = []
    for cell in row.findall(".//th") + row.findall(".//td"):
        text = "".join(cell.itertext()).strip()
        if text:
            cells.append(text)
    return " | ".join(cells)

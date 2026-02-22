from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.render_utils import DEFAULT_PLACEHOLDER, safe_str
from backend.services.synopsis_requirements import REQUIRED_HEADINGS


class DocxWriterError(RuntimeError):
    pass


def write_synopsis_single_table_docx(
    out_path: str,
    synopsis_sections: Dict[str, str],
    sources: List[dict],
) -> None:
    """Пишет один docx: заголовок, одна таблица (все секции в ячейках), под таблицей — список источников."""
    try:
        from docx import Document
        from docx.shared import Pt
    except Exception as exc:
        raise DocxWriterError("python-docx required for single-table synopsis") from exc

    doc = Document()
    # Заголовок
    title = doc.add_paragraph()
    run = title.add_run("СИНОПСИС ПРОТОКОЛА")
    run.bold = True
    run.font.size = Pt(14)
    title.alignment = 1  # WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(12)

    # Таблица: строки = REQUIRED_HEADINGS, колонки = Секция | Содержание
    table = doc.add_table(rows=len(REQUIRED_HEADINGS) + 1, cols=2)
    table.style = "Table Grid"
    header_row = table.rows[0].cells
    header_row[0].text = "Секция"
    header_row[1].text = "Содержание"
    for c in header_row:
        for p in c.paragraphs:
            for r in p.runs:
                r.bold = True
    for i, heading in enumerate(REQUIRED_HEADINGS):
        row = table.rows[i + 1]
        content = synopsis_sections.get(heading, DEFAULT_PLACEHOLDER)
        if heading == "Библиографический список источников":
            content = "См. ниже"
        row.cells[0].text = heading
        row.cells[1].text = (content or DEFAULT_PLACEHOLDER).strip()

    # Под таблицей — список источников
    doc.add_paragraph()
    p_heading = doc.add_paragraph("Библиографический список источников")
    for r in p_heading.runs:
        r.bold = True
    p_heading.paragraph_format.space_before = Pt(12)
    p_heading.paragraph_format.space_after = Pt(6)
    if sources:
        deduped_sources: List[dict] = []
        seen = set()
        for src in sources:
            title_src = safe_str(_get(src, "title"))
            year_src = safe_str(_get(src, "year"))
            ref_id = _source_ref_id(src)
            key = (title_src.lower(), year_src, ref_id)
            if key in seen:
                continue
            seen.add(key)
            deduped_sources.append(src)
        for i, src in enumerate(deduped_sources, 1):
            ref_id = _source_ref_id(src)
            title_src = safe_str(_get(src, "title"))
            year_src = safe_str(_get(src, "year"))
            doc.add_paragraph(f"{i}. {title_src} ({year_src}) {ref_id}")
    else:
        doc.add_paragraph("Источники не указаны.")

    doc.save(out_path)


def ensure_required_headings(
    docx_path: str,
    headings: List[str],
    synopsis_sections: Optional[Dict[str, str]] = None,
) -> None:
    try:
        from docx import Document
        from docx.shared import Pt
    except Exception:
        return

    text = extract_docx_text(docx_path)
    missing = [h for h in headings if h not in text]
    if not missing:
        return

    doc = Document(docx_path)
    doc.add_page_break()
    p_intro = doc.add_paragraph()
    p_intro.add_run("Секции, требуемые CRO (автоматически добавлены)").italic = True
    p_intro.paragraph_format.space_after = Pt(12)

    table = doc.add_table(rows=len(missing) + 1, cols=2)
    table.style = "Table Grid"
    header_cells = table.rows[0].cells
    header_cells[0].text = "Секция"
    header_cells[1].text = "Содержание"
    for cell in header_cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
    for i, heading in enumerate(missing):
        row = table.rows[i + 1]
        content = (synopsis_sections or {}).get(heading) or DEFAULT_PLACEHOLDER
        row.cells[0].text = heading
        row.cells[1].text = content
    doc.save(docx_path)


def ensure_dqi_summary(docx_path: str, dq_summary: str, dq_reasons: str) -> None:
    if not dq_summary or dq_summary == DEFAULT_PLACEHOLDER:
        return
    try:
        from docx import Document
    except Exception:
        return
    doc = Document(docx_path)
    summary_text = safe_str(dq_summary, default=DEFAULT_PLACEHOLDER)
    if summary_text == DEFAULT_PLACEHOLDER:
        return
    parts = [summary_text]
    if dq_reasons and dq_reasons != DEFAULT_PLACEHOLDER:
        reasons_text = safe_str(dq_reasons, default="")
        if reasons_text and reasons_text != DEFAULT_PLACEHOLDER:
            parts.append(f"Причины: {reasons_text}")
    cell_text = "\n".join(parts)
    for table in doc.tables:
        for row in table.rows:
            if not row.cells:
                continue
            if row.cells[0].text.strip() == "Качество данных (DQI)":
                row.cells[1].text = cell_text
                doc.save(docx_path)
                return


def extract_docx_text(docx_path: str) -> str:
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(docx_path) as zf:
            xml = zf.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        texts = [
            node.text
            for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
            if node.text
        ]
        return " ".join(texts)
    except Exception:
        return ""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _source_ref_id(src: Any) -> str:
    """Bibliography ref: PMID:12345678, PMCID:PMC1234567, or URL:<link>. No PMID:PMCID: glue."""
    id_type = _get(src, "id_type")
    id_val = _get(src, "id")
    if id_type and id_val is not None:
        s = safe_str(id_val).strip()
        if not s:
            return f"{id_type}:"
        if id_type == "URL":
            return f"URL:{s}"
        if id_type == "PMCID":
            return f"PMCID:PMC{s}" if s.isdigit() else f"PMCID:{s}"
        if id_type == "PMID":
            return f"PMID:{s}"
        return f"{id_type}:{s}"
    ref_id = _get(src, "ref_id")
    if ref_id and safe_str(ref_id).strip():
        return _format_reference_id(ref_id)
    pmcid = _get(src, "pmcid")
    if pmcid and safe_str(pmcid).strip():
        p = safe_str(pmcid).strip().lstrip("PMC")
        return f"PMCID:PMC{p}" if p.isdigit() else f"PMCID:{pmcid}"
    return _format_reference_id(_get(src, "pmid"))


def _format_reference_id(raw_id: Any) -> str:
    text = safe_str(raw_id).strip()
    if text == DEFAULT_PLACEHOLDER:
        return f"PMID:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return f"URL:{text}"
    upper = text.upper()
    if upper.startswith("PMCID:"):
        rest = text.split(":", 1)[1].strip().lstrip("PMC")
        return f"PMCID:PMC{rest}" if rest.isdigit() else f"PMCID:{rest}"
    if upper.startswith("PMID:"):
        return f"PMID:{text.split(':', 1)[1].strip()}"
    if upper.startswith("URL:"):
        return f"URL:{text.split(':', 1)[1].strip()}"
    return f"PMID:{text}"

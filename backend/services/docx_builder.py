from __future__ import annotations

import os
import re
from typing import Dict

from docxtpl import DocxTemplate

from backend.services.utils import now_iso


def build_docx(all_json: Dict) -> str:
    template_path = os.path.join("templates", "synopsis_template.docx")
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    doc = DocxTemplate(template_path)
    inn = str(all_json.get("inn") or all_json.get("search", {}).get("inn") or "unknown")
    safe_inn = re.sub(r"[^a-zA-Z0-9_-]+", "_", inn).strip("_")
    context = {
        "generated_at": now_iso(),
        "inn": inn,
        "data": all_json,
    }
    doc.render(context)

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"synopsis_{safe_inn}.docx")
    doc.save(out_path)
    return out_path

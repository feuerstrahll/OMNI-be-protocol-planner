from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger(__name__)


class YandexLLMClient:
    def __init__(self, api_key: Optional[str] = None, folder_id: Optional[str] = None, model: str = "yandexgpt-pro") -> None:
        self.api_key = api_key or os.getenv("YANDEX_API_KEY")
        self.folder_id = folder_id or os.getenv("YANDEX_FOLDER_ID")
        self.model = model
        self.base_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def extract_pk_from_text(self, text: str, inn: str) -> Dict[str, Any]:
        if not self.api_key or not self.folder_id:
            return {}
        try:
            truncated = (text or "")[:6000]
            payload = {
                "modelUri": f"gpt://{self.folder_id}/{self.model}",
                "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 800},
                "messages": [
                    {
                        "role": "system",
                        "text": (
                            "You are a pharmacokinetics data extraction assistant. "
                            "Extract numeric PK values from the provided text. "
                            "Return ONLY valid JSON, no explanation."
                        ),
                    },
                    {
                        "role": "user",
                        "text": (
                            f"Drug: {inn}\n\nText:\n{truncated}\n\n"
                            "Extract all available PK parameters and return JSON:\n"
                            "{\n"
                            '  "CVintra": null or number (intra-subject CV in %),\n'
                            '  "Cmax": null or number,\n'
                            '  "Cmax_unit": null or string,\n'
                            '  "AUC": null or number,\n'
                            '  "AUC_unit": null or string,\n'
                            '  "t_half": null or number,\n'
                            '  "Tmax": null or number,\n'
                            '  "CI_low": null or number (90% CI lower bound for AUC or Cmax GMR),\n'
                            '  "CI_high": null or number,\n'
                            '  "CI_param": null or "AUC" or "Cmax",\n'
                            '  "n": null or integer (number of subjects),\n'
                            '  "feeding_condition": null or "fasted" or "fed",\n'
                            '  "study_type": null or "human" or "animal",\n'
                            '  "design": null or "2x2_crossover" or "parallel" or "other"\n'
                            "}\n"
                            "Rules:\n"
                            "- CVintra means intra-subject (within-subject) CV only, NOT inter-subject\n"
                            "- If a value is not mentioned, return null\n"
                            "- Do not invent numbers\n"
                            "- Return only the JSON object, nothing else"
                        ),
                    },
                ],
            }
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
                "Content-Type": "application/json",
            }
            resp = requests.post(self.base_url, headers=headers, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.warning("yandex_llm_http_error", status=resp.status_code, text=resp.text[:200])
                return {}
            data = resp.json()
            text_out = (
                data.get("result", {})
                .get("alternatives", [{}])[0]
                .get("message", {})
                .get("text", "")
            )
            match = re.search(r"\{.*\}", text_out, re.DOTALL)
            if not match:
                return {}
            return json.loads(match.group(0))
        except Exception as exc:
            logger.warning("yandex_llm_error", error=str(exc))
            return {}

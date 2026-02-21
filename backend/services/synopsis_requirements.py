from __future__ import annotations

from typing import Any, Dict, List


REQUIRED_HEADINGS: List[str] = [
    # Header / Metadata
    "Название клинического исследования",
    "Номер исследования",
    "Спонсор исследования",
    "Фаза клинического исследования",
    "Действующее вещество",
    "Лекарственная форма и дозировка",
    # Core protocol
    "Цель исследования",
    "Задачи исследования",
    "Первичные конечные точки",
    "Вторичные конечные точки",
    "Дизайн исследования",
    "Обоснование дизайна",
    "Методология исследования",
    "Исследуемая популяция",
    "Критерии включения",
    "Критерии невключения",
    # PK, Stats & Safety
    "Фармакокинетические параметры",
    "Биоаналитические методы",
    "Временная структура исследования (таймпоинты)",
    "Размер выборки",
    "Рандомизация",
    "Статистические методы",
    "План мониторинга безопасности",
    # Regulatory & AI-specific
    "Этические и регуляторные аспекты",
    "Качество данных (DQI)",
    "Регуляторные замечания / Open Questions",
    "Библиографический список источников",
]


HEADING_FIELD_MAP: Dict[str, List[str]] = {
    "Название клинического исследования": ["study.title", "study_title", "title"],
    "Номер исследования": ["study.protocol_id", "protocol_id"],
    "Спонсор исследования": ["study.sponsor", "sponsor"],
    "Фаза клинического исследования": ["study.study_phase", "study_phase", "study.phase", "phase"],
    "Действующее вещество": ["study.inn", "inn"],
    "Лекарственная форма и дозировка": ["study.dosage_form", "dosage_form", "study.dose", "dose"],
    "Цель исследования": ["study.objective", "objective", "objectives"],
    "Задачи исследования": ["study.tasks", "tasks"],
    "Первичные конечные точки": ["study.primary_endpoints", "primary_endpoints"],
    "Вторичные конечные точки": ["study.secondary_endpoints", "secondary_endpoints"],
    "Дизайн исследования": ["study.design.recommended", "design.recommendation", "design"],
    "Обоснование дизайна": ["design.reasoning_text", "study.design.reasoning"],
    "Методология исследования": [
        "study.methodology",
        "methodology",
        "study.sampling_schedule",
        "study.total_blood_volume_ml",
    ],
    "Исследуемая популяция": [
        "study.population",
        "population",
        "study.gender_requirement",
        "gender_requirement",
        "study.age_range",
        "age_range",
    ],
    "Критерии включения": ["study.inclusion_criteria", "inclusion_criteria"],
    "Критерии невключения": ["study.exclusion_criteria", "exclusion_criteria"],
    "Фармакокинетические параметры": [
        "pk.pk_values",
        "pk_values",
        "study.pk_parameters",
    ],
    "Биоаналитические методы": ["study.bioanalytical_methods", "bioanalytical_methods"],
    "Временная структура исследования (таймпоинты)": [
        "study.sampling_schedule",
        "sampling_schedule",
    ],
    "Размер выборки": [
        "study.sample_size",
        "sample_size",
        "sample_size_det",
        "sample_size_det.n_total",
        "sample_size_risk",
    ],
    "Рандомизация": ["study.randomization", "randomization"],
    "Статистические методы": ["study.statistical_methods", "statistical_methods"],
    "План мониторинга безопасности": ["study.safety", "safety"],
    "Этические и регуляторные аспекты": ["study.ethics", "ethics"],
    "Качество данных (DQI)": ["dqi", "data_quality"],
    "Регуляторные замечания / Open Questions": [
        "reg_check",
        "open_questions",
        "reg_check_summary",
    ],
    "Библиографический список источников": ["sources"],
}


def evaluate_synopsis_completeness(full_report: Dict[str, Any]) -> Dict[str, Any]:
    missing_fields: List[str] = []
    for heading in REQUIRED_HEADINGS:
        paths = HEADING_FIELD_MAP.get(heading, [])
        if not _any_present(full_report, paths):
            missing_fields.append(heading)

    missing_headings: List[str] = []
    level = "green"
    if missing_fields:
        level = "yellow"
    if len(missing_fields) >= max(6, len(REQUIRED_HEADINGS) // 2):
        level = "red"

    notes: List[str] = []
    if missing_fields:
        notes.append(
            "Missing fields for sections: " + ", ".join(missing_fields[:8])
        )

    return {
        "missing_fields": missing_fields,
        "missing_headings": missing_headings,
        "level": level,
        "notes": notes,
    }


def _any_present(data: Dict[str, Any], paths: List[str]) -> bool:
    for path in paths:
        value = _get_path(data, path)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, dict) and not value:
            continue
        return True
    return False


def _get_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current

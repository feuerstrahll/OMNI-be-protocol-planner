from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.render_utils import DEFAULT_PLACEHOLDER, safe_join, safe_num, safe_str
from backend.services.synopsis_requirements import HEADING_FIELD_MAP, REQUIRED_HEADINGS


AUTO_FILLED_HEADINGS = {
    "Обоснование дизайна",
    "Цель исследования",
    "Задачи исследования",
    "Первичные конечные точки",
    "Вторичные конечные точки",
    "Исследуемая популяция",
    "Критерии включения",
    "Критерии невключения",
    "Дизайн исследования",
    "Фаза клинического исследования",
    "Биоаналитические методы",
    "Временная структура исследования (таймпоинты)",
    "Фармакокинетические параметры",
    "Статистические методы",
    "План мониторинга безопасности",
    "Размер выборки",
    "Рандомизация",
    "Библиографический список источников",
    "Методология исследования",
    "Этические и регуляторные аспекты",
    "Действующее вещество",
    "Лекарственная форма и дозировка",
}


def build_synopsis_sections(
    report: Dict[str, Any],
    dq_summary: str,
    open_questions_table: List[dict],
    sample_size_line: str,
) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    study = report.get("study") or {}
    design_obj = report.get("design") or (study.get("design") or {})
    pk_values = _as_list(report.get("pk_values") or _get(report.get("pk") or {}, "pk_values"))
    sampling_schedule = report.get("sampling_schedule") or study.get("sampling_schedule") or []
    bio_methods = report.get("bioanalytical_methods") or study.get("bioanalytical_methods")
    randomization = report.get("randomization") or study.get("randomization")
    washout_raw = study.get("washout_days") or report.get("schedule_days")
    washout_days = safe_num(washout_raw) if washout_raw is not None else DEFAULT_PLACEHOLDER
    periods_raw = study.get("periods_count") or report.get("periods_count")
    periods_count: Optional[int] = None
    if periods_raw is not None:
        try:
            periods_count = int(float(periods_raw))
        except Exception:
            periods_count = None

    rec_design = safe_str(
        _get(design_obj, "recommendation") or _get(design_obj, "recommended"),
        default=DEFAULT_PLACEHOLDER,
    )
    if rec_design == DEFAULT_PLACEHOLDER:
        rec_design = "2x2_crossover (базовый вариант)"

    inferred_periods, inferred_sequences = _infer_periods_and_sequences(rec_design)
    if periods_count is None:
        periods_count = inferred_periods

    sequences = study.get("sequences") or report.get("sequences") or inferred_sequences
    if isinstance(sequences, list):
        sequences_text = safe_join(sequences, default=DEFAULT_PLACEHOLDER)
    else:
        sequences_text = safe_str(sequences, default=DEFAULT_PLACEHOLDER)

    phase_label = _map_study_phase(report.get("study_phase") or study.get("study_phase"))
    protocol_condition = safe_str(report.get("protocol_condition") or study.get("protocol_condition"))

    if isinstance(sampling_schedule, list) and sampling_schedule:
        timepoints_text = safe_join(sampling_schedule, sep=", ", default=DEFAULT_PLACEHOLDER)
    elif isinstance(sampling_schedule, str) and sampling_schedule.strip():
        timepoints_text = sampling_schedule.strip()
    else:
        timepoints_text = (
            "Преддозовый и постдозовые таймпоинты, охватывающие Tmax и терминальную фазу "
            "(не менее 3-5 t1/2); финальная точка для AUC0-t."
        )

    bio_methods_text = safe_str(bio_methods, default="")
    if not bio_methods_text or bio_methods_text == DEFAULT_PLACEHOLDER:
        bio_methods_text = (
            "Количественное определение концентраций в плазме валидированным методом (например, LC-MS/MS) "
            "с оценкой точности, прецизионности, LLOQ и стабильности."
        )

    population_default = "Здоровые добровольцы; пол и возраст уточняются."
    inclusion_default = (
        "Возраст 18-55 лет.\n"
        "ИМТ 18-30 кг/м2.\n"
        "Отсутствие клинически значимых отклонений по анамнезу, осмотру и лабораторным данным.\n"
        "Подписанное информированное согласие."
    )
    exclusion_default = (
        "Гиперчувствительность к исследуемому препарату/аналогам.\n"
        "Клинически значимые сопутствующие заболевания.\n"
        "Прием лекарств, влияющих на PK, в период скрининга/исследования.\n"
        "Беременность или лактация."
    )
    safety_default = (
        "Мониторинг НЯ/СНЯ, витальных показателей, ЭКГ и лабораторных параметров "
        "на протяжении всего исследования и периода наблюдения."
    )
    ethics_default = (
        "Исследование проводится в соответствии с GCP и Хельсинкской декларацией; "
        "перед включением — информированное согласие и одобрение ЛЭК."
    )
    methodology_default = (
        "Скрининг; период(ы) дозирования; отмывка; период наблюдения. "
        "Отбор образцов крови для PK по утвержденному расписанию."
    )

    for heading in REQUIRED_HEADINGS:
        value = _first_value(report, HEADING_FIELD_MAP.get(heading, []))

        if heading == "Качество данных (DQI)":
            value = dq_summary or value
        elif heading == "Регуляторные замечания / Open Questions":
            value = safe_join([q.get("question") for q in open_questions_table], default=DEFAULT_PLACEHOLDER)
        elif heading == "Размер выборки":
            value = sample_size_line or value
        elif heading == "Лекарственная форма и дозировка":
            form = safe_str(report.get("dosage_form") or study.get("dosage_form"))
            d = safe_str(report.get("dose") or study.get("dose"))
            parts = [p for p in [form, d] if p != DEFAULT_PLACEHOLDER]
            value = ", ".join(parts) if parts else None
        elif heading == "Фаза клинического исследования":
            if _is_missing_value(value):
                value = "Исследование биоэквивалентности"
        elif heading == "Действующее вещество":
            if _is_missing_value(value):
                value = report.get("inn") or study.get("inn")
        elif heading == "Обоснование дизайна":
            reasoning = _get(design_obj, "reasoning_text") or _get(design_obj, "reasoning")
            if isinstance(reasoning, list):
                reasoning = safe_join(reasoning, sep="; ", default=DEFAULT_PLACEHOLDER)
            value = safe_str(reasoning, default=DEFAULT_PLACEHOLDER)
            if value == DEFAULT_PLACEHOLDER:
                value = (
                    "Обоснование основано на регуляторных требованиях к BE, "
                    "оценке вариабельности (CVintra), PK-параметров (Cmax/AUC), "
                    "а также длительности t1/2 и режима fed/fasted."
                )
        elif heading == "Дизайн исследования":
            parts = [f"Дизайн: {rec_design}"]
            if phase_label != DEFAULT_PLACEHOLDER:
                parts.append(f"Тип/фаза: {phase_label}")
            if protocol_condition != DEFAULT_PLACEHOLDER:
                parts.append(f"Режим приёма: {protocol_condition}")
            if periods_count is not None:
                parts.append(f"Периоды: {periods_count}")
            if sequences_text != DEFAULT_PLACEHOLDER:
                parts.append(f"Последовательности: {sequences_text}")
            if washout_days != DEFAULT_PLACEHOLDER:
                parts.append(f"Период отмывки: {washout_days} дн.")
            rand_text = safe_str(randomization, default="")
            if not rand_text or rand_text == DEFAULT_PLACEHOLDER:
                rand_text = _default_randomization(rec_design, sequences_text)
            parts.append(f"Рандомизация: {rand_text}")
            value = "; ".join(parts)
        elif heading == "Исследуемая популяция":
            gender = safe_str(report.get("gender_requirement") or study.get("gender_requirement"))
            age = safe_str(report.get("age_range") or study.get("age_range"))
            constraints = safe_str(report.get("additional_constraints") or study.get("additional_constraints"))
            parts = []
            if gender != DEFAULT_PLACEHOLDER:
                parts.append(f"Пол: {gender}")
            if age != DEFAULT_PLACEHOLDER:
                parts.append(f"Возраст: {age}")
            if constraints != DEFAULT_PLACEHOLDER:
                parts.append(f"Ограничения: {constraints}")
            value = "; ".join(parts) if parts else population_default
        elif heading == "Библиографический список источников":
            sources = _as_list(report.get("sources"))
            if sources:
                refs = []
                for i, s in enumerate(sources, 1):
                    pmid = safe_str(_get(s, "pmid"))
                    title = safe_str(_get(s, "title"))
                    year = safe_str(_get(s, "year"))
                    refs.append(f"{i}. {title} ({year}) PMID:{pmid}")
                value = "\n".join(refs)
            else:
                value = "Источники не указаны."
        elif heading == "Цель исследования":
            if _is_missing_value(value):
                inn_val = safe_str(report.get("inn") or study.get("inn"))
                value = (
                    f"Оценка биоэквивалентности тестового и референтного препаратов "
                    f"действующего вещества {inn_val} у здоровых добровольцев."
                )
        elif heading == "Задачи исследования":
            if _is_missing_value(value):
                value = (
                    "1. Определить фармакокинетические параметры (Cmax, AUC0-t, AUC0-inf) тестового и референтного препаратов.\n"
                    "2. Провести статистическое сравнение PK-параметров для оценки биоэквивалентности.\n"
                    "3. Оценить безопасность и переносимость препаратов."
                )
        elif heading == "Первичные конечные точки":
            if _is_missing_value(value):
                value = "Cmax, AUC0-t (90% ДИ отношения геометрических средних: 80.00–125.00%)."
        elif heading == "Вторичные конечные точки":
            if _is_missing_value(value):
                value = "Tmax, t1/2, AUC0-inf (при применимости), показатели безопасности."
        elif heading == "Критерии включения":
            if _is_missing_value(value):
                value = inclusion_default
        elif heading == "Критерии невключения":
            if _is_missing_value(value):
                value = exclusion_default
        elif heading == "Фармакокинетические параметры":
            if _is_missing_value(value):
                value = _summarize_pk_values(pk_values)
        elif heading == "Биоаналитические методы":
            if _is_missing_value(value):
                value = bio_methods_text
        elif heading == "Временная структура исследования (таймпоинты)":
            if _is_missing_value(value):
                value = timepoints_text
        elif heading == "Рандомизация":
            if _is_missing_value(value):
                value = _default_randomization(rec_design, sequences_text)
        elif heading == "План мониторинга безопасности":
            if _is_missing_value(value):
                value = safety_default
        elif heading == "Этические и регуляторные аспекты":
            if _is_missing_value(value):
                value = ethics_default
        elif heading == "Методология исследования":
            if _is_missing_value(value):
                parts = [methodology_default]
                hosp_days = safe_num(report.get("hospitalization_duration_days"))
                sampling_days = safe_num(report.get("sampling_duration_days"))
                follow_days = safe_num(report.get("follow_up_duration_days"))
                phone_follow = report.get("phone_follow_up_ok")
                blood_total = safe_num(report.get("blood_volume_total_ml") or study.get("total_blood_volume_ml"))
                blood_pk = safe_num(report.get("blood_volume_pk_ml"))
                if hosp_days != DEFAULT_PLACEHOLDER:
                    parts.append(f"Госпитализация: {hosp_days} дн.")
                if sampling_days != DEFAULT_PLACEHOLDER:
                    parts.append(f"Длительность отбора: {sampling_days} дн.")
                if follow_days != DEFAULT_PLACEHOLDER:
                    parts.append(f"Наблюдение: {follow_days} дн.")
                if phone_follow is True:
                    parts.append("Телефонное наблюдение допускается.")
                elif phone_follow is False:
                    parts.append("Телефонное наблюдение не допускается.")
                if blood_total != DEFAULT_PLACEHOLDER:
                    parts.append(f"Общий объём крови: {blood_total} мл.")
                if blood_pk != DEFAULT_PLACEHOLDER:
                    parts.append(f"Объём крови на PK: {blood_pk} мл.")
                value = " ".join(parts)
        elif heading == "Статистические методы":
            if _is_missing_value(value):
                value = (
                    "Дисперсионный анализ (ANOVA) логарифмически преобразованных PK-параметров. "
                    "Расчёт 90% ДИ для отношения геометрических средних Test/Reference. "
                    "Критерий биоэквивалентности: 80.00% – 125.00% (EAEU, Decision 85)."
                )

        sections[heading] = _format_heading_value(value)
    return sections


def _as_list(value: Any) -> List[dict]:
    if value is None:
        return []
    return list(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _format_heading_value(value: Any) -> str:
    if value is None:
        return DEFAULT_PLACEHOLDER
    if isinstance(value, (list, tuple)):
        return safe_join(value, default=DEFAULT_PLACEHOLDER)
    if isinstance(value, dict):
        if "recommended" in value:
            return safe_str(value.get("recommended"), default=DEFAULT_PLACEHOLDER)
        if "recommendation" in value:
            return safe_str(value.get("recommendation"), default=DEFAULT_PLACEHOLDER)
        return safe_str(value, default=DEFAULT_PLACEHOLDER)
    return safe_str(value, default=DEFAULT_PLACEHOLDER)


def _first_value(report: Dict[str, Any], paths: List[str]) -> Any:
    for path in paths:
        value = _get_path(report, path)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, dict) and not value:
            continue
        return value
    return None


def _get_path(data: Any, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ("", DEFAULT_PLACEHOLDER)
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _summarize_pk_values(pk_values: List[dict]) -> str:
    values = _as_list(pk_values)
    if not values:
        return "Cmax, AUC0-t, AUC0-inf (при применимости), Tmax, t1/2."
    parts: List[str] = []
    for item in values:
        name = safe_str(_get(item, "name"))
        if name == DEFAULT_PLACEHOLDER:
            continue
        raw_val = _get(item, "value")
        if raw_val is None:
            parts.append(name)
            continue
        val = safe_num(raw_val)
        unit = safe_str(_get(item, "unit"), default="")
        if unit and unit != DEFAULT_PLACEHOLDER:
            parts.append(f"{name}: {val} {unit}")
        else:
            parts.append(f"{name}: {val}")
    return "; ".join(parts) if parts else "Cmax, AUC0-t, AUC0-inf (при применимости), Tmax, t1/2."


def _map_study_phase(phase: Any) -> str:
    if phase is None:
        return DEFAULT_PLACEHOLDER
    value = safe_str(phase)
    if value == DEFAULT_PLACEHOLDER:
        return DEFAULT_PLACEHOLDER
    lookup = {
        "single": "однофазное",
        "two-phase": "двухфазное",
        "auto": "по умолчанию",
    }
    return lookup.get(value, value)


def _infer_periods_and_sequences(design: str) -> tuple[Optional[int], Optional[str]]:
    if not design or design == DEFAULT_PLACEHOLDER:
        return None, None
    text = design.lower()
    if "parallel" in text:
        return 1, "T vs R (параллельные группы)"
    if "2x2x4" in text or "4-way" in text or "4way" in text or "full replicate" in text:
        return 4, "TRTR / RTRT"
    if "2x2x3" in text or "3-period" in text or "partial replicate" in text:
        return 3, "TRR / RTR"
    if "replicate" in text:
        return 4, "TRTR / RTRT"
    if "2x2" in text or "crossover" in text:
        return 2, "TR / RT"
    return None, None


def _default_randomization(design: str, sequences: Optional[str]) -> str:
    if sequences and sequences != DEFAULT_PLACEHOLDER:
        return f"Рандомизация 1:1 по последовательностям ({sequences})."
    if design and design != DEFAULT_PLACEHOLDER:
        text = design.lower()
        if "parallel" in text:
            return "Рандомизация 1:1 между группами Test/Reference."
        if "crossover" in text or "2x2" in text or "replicate" in text:
            return "Рандомизация 1:1 по последовательностям (TR/RT)."
    return "Рандомизация 1:1 между группами/последовательностями (уточняется)."

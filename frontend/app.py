import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Маппинг русских подписей в значения API
PROTOCOL_CONDITION_RU_TO_API = {"": None, "натощак": "fasted", "после еды": "fed", "оба варианта": "both"}
PROTOCOL_CONDITION_API_TO_RU = {None: "", "fasted": "натощак", "fed": "после еды", "both": "оба варианта"}
STUDY_PHASE_RU_TO_API = {"автовыбор моделью": None, "однопериодное": "single", "двухпериодное": "two-phase"}
STUDY_PHASE_OPTIONS_RU = ["автовыбор моделью", "однопериодное", "двухпериодное"]
PREFERRED_DESIGN_OPTIONS_RU = [
    ("Автовыбор", ""),
    ("2×2 кроссовер", "2x2_crossover"),
    ("репликат", "replicate"),
    ("4-кратная репликация", "4-way_replicate"),
    ("параллельный", "parallel"),
]

# Единый текст инструкции (используется в expander и рядом с Run pipeline)
WORKFLOW_INSTRUCTIONS = (
    "**Порядок работы с системой**\n\n"
    "1. **INN** — введите МНН, при желании нажмите «Найти источники (PubMed/PMC)» и отметьте релевантные статьи в списке ниже.\n\n"
    "2. **Метаданные** — форма, доза, режим (натощак / после еды / не знаю), NTI/RSABE. Пол и возраст — в блоке Advanced (не обязательны для Run pipeline).\n\n"
    "3. **CVintra** — авто из литературы или вручную (20/30/40/50% или своё число). Подтверждение желательно для финализации; без него N_det может считаться как provisional. Если CV отсутствует или заблокирован качеством данных — появится причина в Open Questions.\n\n"
    "4. **Параметры расчёта** — power, alpha, dropout, screen-fail. Можно оставить по умолчанию.\n\n"
    "5. **Регуляторные параметры** (опционально) — washout, длительности, объём крови. Если пусто — появятся Open Questions.\n\n"
    "6. **Run pipeline** — нажмите кнопку: поиск → PK/CV → дизайн → N → рег. проверки → Open Questions.\n\n"
    "7. **Просмотр результатов** — дизайн, N_det/N_risk, DQI, проверки, открытые вопросы (секции 2–6 ниже).\n\n"
    "8. **Экспорт** — .docx / .json / .md (секция 8).\n\n"
    "Кнопки «Подобрать дизайн» и «Рассчитать N_det» — для пошагового режима и отладки."
)

st.set_page_config(page_title="Планирование БЭ — прототип", layout="wide")
st.title("Планирование исследований биоэквивалентности (БЭ)")


def api_post(path: str, payload: dict, timeout: int = 120) -> dict:
    try:
        resp = requests.post(
            f"{BACKEND_URL}{path}",
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Не удалось подключиться к бекенду: {BACKEND_URL}")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Превышено время ожидания ({timeout}с) для {path}")

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"[{resp.status_code}] {detail}")

    return resp.json()


def approx_n_total(cv_percent: float, power: float, alpha: float) -> int:
    import math

    cv = cv_percent / 100.0
    sigma = math.sqrt(math.log(1 + cv * cv))
    z_alpha = _inv_norm_cdf(1 - alpha)
    z_beta = _inv_norm_cdf(power)
    delta = math.log(1.25)
    n_total = math.ceil(((z_alpha + z_beta) * math.sqrt(2) * sigma / delta) ** 2)
    return max(2, n_total)


def _inv_norm_cdf(p: float) -> float:
    import math

    # Acklam approximation
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]

    plow = 0.02425
    phigh = 1 - plow

    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    if phigh < p:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )

    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value)


def _get(dct: Optional[Dict], key: str, default: Any = None) -> Any:
    if not dct:
        return default
    return dct.get(key, default)


def _resolve_cv_context(fullreport: Optional[Dict], pk: Optional[Dict]) -> Tuple[str, Optional[float], List[Dict], Dict]:
    cv_info = (fullreport or {}).get("cv_info") or {}
    if cv_info:
        cv_source = cv_info.get("cv_source") or cv_info.get("source") or "unknown"
        return cv_source, cv_info.get("value"), cv_info.get("evidence") or [], cv_info

    if pk:
        for pkv in pk.get("pk_values", []):
            if pkv.get("name") == "CVintra":
                return "reported", pkv.get("value"), pkv.get("evidence") or [], {}

    return "unknown", None, [], {}


def _find_ci_for_cv(ci_values: List[Dict]) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    for ci in ci_values:
        level = ci.get("confidence_level")
        if level is None or abs(float(level) - 0.90) <= 0.02:
            return ci.get("ci_low"), ci.get("ci_high"), ci.get("n")
    return None, None, None


def _render_evidence(evidence_list: List[Dict]) -> None:
    if not evidence_list:
        st.caption("Данные отсутствуют.")
        return
    for ev in evidence_list:
        excerpt = ev.get("excerpt") or ev.get("snippet") or "Данные отсутствуют."
        source = ev.get("pmid_or_url") or ev.get("pmid") or ev.get("url") or ev.get("source")
        pmid = ev.get("pmid")
        if not pmid and isinstance(source, str) and source.isdigit():
            pmid = source
        st.caption(excerpt)
        if pmid:
            st.markdown(f"Источник: PMID [{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)")
        elif source:
            st.caption(f"Источник: {source}")


def _format_design(fullreport: Optional[Dict], design_resp: Optional[Any]) -> Dict:
    if fullreport and fullreport.get("design") is not None:
        design_obj = fullreport["design"]
        if isinstance(design_obj, dict):
            if "design" not in design_obj and "recommendation" in design_obj:
                return {"design": design_obj.get("recommendation"), **design_obj}
            return design_obj
        if isinstance(design_obj, str):
            return {"design": design_obj}

    if isinstance(design_resp, dict):
        if "design" in design_resp:
            return design_resp
        if "recommendation" in design_resp:
            return {"design": design_resp.get("recommendation"), **design_resp}
    if isinstance(design_resp, str):
        return {"design": design_resp}
    return {}


def _reset_cv_on_inn_change() -> None:
    """При смене МНН сбрасываем CV и English INN, чтобы не использовать данные другого препарата."""
    st.session_state["cv_confirmed"] = False
    st.session_state["manual_cv"] = None
    st.session_state["inn_en_input"] = ""
    st.session_state["inn_en"] = ""
    st.session_state["inn_en_confirmed"] = False


if "sources" not in st.session_state:
    st.session_state["sources"] = []
if "pk" not in st.session_state:
    st.session_state["pk"] = None
if "design" not in st.session_state:
    st.session_state["design"] = None
if "sample" not in st.session_state:
    st.session_state["sample"] = None
if "variability" not in st.session_state:
    st.session_state["variability"] = None
if "risk" not in st.session_state:
    st.session_state["risk"] = None
if "reg" not in st.session_state:
    st.session_state["reg"] = None
if "fullreport" not in st.session_state:
    st.session_state["fullreport"] = None
if "docx_bytes" not in st.session_state:
    st.session_state["docx_bytes"] = None
if "docx_filename" not in st.session_state:
    st.session_state["docx_filename"] = None
if "docx_error" not in st.session_state:
    st.session_state["docx_error"] = None
# Дефолты для метаданных протокола (блок Advanced ниже; payload читает отсюда)
if "protocol_id" not in st.session_state:
    st.session_state["protocol_id"] = ""
if "visit_day_numbering" not in st.session_state:
    st.session_state["visit_day_numbering"] = "continuous across periods"
if "replacement_subjects_label" not in st.session_state:
    st.session_state["replacement_subjects_label"] = "Нет"
if "study_phase_label" not in st.session_state:
    st.session_state["study_phase_label"] = "автовыбор моделью"
if "gender_requirement" not in st.session_state:
    st.session_state["gender_requirement"] = ""
if "age_range" not in st.session_state:
    st.session_state["age_range"] = "18-45"
if "additional_constraints" not in st.session_state:
    st.session_state["additional_constraints"] = ""


with st.expander("📋 Порядок работы с системой", expanded=False):
    st.markdown(WORKFLOW_INSTRUCTIONS)

st.subheader("Шаг 1 — Препарат и режим исследования (INN)")
inn = st.text_input(
    "Международное непатентованное название (INN)",
    value="метформин",
    key="inn",
    on_change=_reset_cv_on_inn_change,
    help="Например: метформин, будесонид",
)

# ── Нормализация INN: русский → English для PubMed ─────────────────────────
if "inn_en_input" not in st.session_state:
    st.session_state["inn_en_input"] = ""
if "inn_en" not in st.session_state:
    st.session_state["inn_en"] = ""
if "inn_en_confirmed" not in st.session_state:
    st.session_state["inn_en_confirmed"] = False

# Применить отложенное значение EN INN до создания виджета (Streamlit не даёт менять key виджета после создания)
if "_inn_en_pending" in st.session_state:
    st.session_state["inn_en_input"] = st.session_state.pop("_inn_en_pending")
    st.session_state["inn_en_confirmed"] = True


def _is_latin(s: str) -> bool:
    return all(ord(c) < 128 for c in (s or "").replace(" ", "").replace("-", ""))


col_inn1, col_inn2 = st.columns([3, 1])
with col_inn1:
    st.text_input(
        "English INN для PubMed (заполняется автоматически)",
        value=st.session_state.get("inn_en_input") or "",
        key="inn_en_input",
        help="Можно отредактировать вручную при необходимости",
    )

with col_inn2:
    if st.button("🔄 Определить INN EN"):
        inn_raw = st.session_state.get("inn", "").strip()
        if not inn_raw:
            st.warning("Введите МНН препарата.")
        elif _is_latin(inn_raw):
            st.session_state["_inn_en_pending"] = inn_raw.lower()
            st.success(f"INN: {inn_raw.lower()}")
            st.rerun()
        else:
            try:
                resp = api_post("/translate_inn", {"inn_ru": inn_raw})
                translated = (resp.get("inn_en") or "").strip().lower()
                if translated:
                    st.session_state["_inn_en_pending"] = translated
                    st.success(f"Переведено: {inn_raw} → **{translated}**")
                    syns = resp.get("synonyms", [])
                    if syns:
                        st.caption(f"Синонимы: {', '.join(syns[:3])}")
                    st.rerun()
                else:
                    st.error("Не удалось определить English INN. Введите вручную.")
            except Exception as exc:
                st.error(f"Ошибка трансляции: {exc}")

# ── Дополнительные поля шага 1 ─────────────────────────────────────────────
col_df1, col_df2, col_df3 = st.columns([2, 1, 1])
with col_df1:
    dosage_form = st.selectbox(
        "Лекарственная форма",
        ["", "таблетки", "капсулы", "раствор", "суспензия", "спрей", "гель", "порошок"],
        key="step1_dosage_form",
    )
st.session_state["dosage_form"] = dosage_form
with col_df2:
    dose_value = st.number_input("Доза", min_value=0.0, step=1.0, format="%.2f", key="step1_dose_value")
with col_df3:
    dose_unit = st.selectbox("Единицы", ["mg", "mcg", "g"], index=0, key="step1_dose_unit")

if dose_value and dose_unit:
    st.session_state["dose"] = f"{dose_value:g} {dose_unit}"

col_protocol1, col_protocol2 = st.columns([2, 2])
with col_protocol1:
    protocol_condition_label = st.radio(
        "Режим приёма",
        ["Натощак", "После еды", "Не знаю"],
        horizontal=True,
        key="step1_protocol_condition_ui",
    )
    protocol_condition_map = {
        "Натощак": "fasted",
        "После еды": "fed",
        "Не знаю": None,
    }
    st.session_state["protocol_condition"] = protocol_condition_map.get(protocol_condition_label)
with col_protocol2:
    study_type = st.radio(
        "Тип исследования",
        ["In vivo BE на здоровых добровольцах", "Другое (advanced)"],
        horizontal=False,
        key="step1_study_type",
    )

col_flags1, col_flags2 = st.columns([2, 2])
with col_flags1:
    nti_choice = st.radio(
        "NTI (узкое терапевтическое окно)",
        ["Не уверен", "NTI"],
        horizontal=True,
        key="nti_choice",
    )
    st.session_state["nti"] = True if nti_choice == "NTI" else None
with col_flags2:
    st.session_state["rsabe_requested"] = st.checkbox(
        "Рассмотреть RSABE (если HVD)",
        value=st.session_state.get("rsabe_requested", False),
        key="rsabe_requested_new",
    )

# Одна кнопка поиска источников (Find sources)
if st.button("Найти источники (PubMed/PMC)"):
    try:
        resp = api_post(
            "/search_sources",
            {
                "inn": (st.session_state.get("inn_en_input") or "").strip().lower() or st.session_state.get("inn", ""),
                "inn_ru": st.session_state.get("inn", "") or None,
                "retmax": 10,
            },
        )
        st.session_state["sources"] = resp.get("sources", [])
        st.session_state["search"] = resp
        def _source_id(s):
            if s.get("id_type") and s.get("id") is not None:
                return f"{s.get('id_type')}:{s.get('id')}"
            return s.get("ref_id") or s.get("pmid")
        st.session_state["selected_sources"] = [_source_id(s) for s in st.session_state["sources"]]
        st.success("Источники найдены. Отметьте релевантные ниже или перейдите к Run pipeline.")
    except Exception as exc:
        st.error(f"Поиск не удался: {exc}")

inn_ru = st.session_state.get("inn", "").strip()
inn_en = (st.session_state.get("inn_en_input") or "").strip().lower()
# keep legacy key for downstream code that reads inn_en
st.session_state["inn_en"] = inn_en
inn_for_api = inn_en or inn_ru

if inn_ru and not inn_en:
    st.warning("⚠️ Нажмите «🔄 Определить INN EN» перед поиском в PubMed.")

with st.expander("Поиск источников (PubMed/PMC)", expanded=False):
    st.caption("Поиск выполняется кнопкой **«Найти источники (PubMed/PMC)»** выше. Здесь — просмотр и выбор релевантных статей.")
    sources = st.session_state.get("sources", [])
    if sources:
        def _source_id(s):
            if s.get("id_type") and s.get("id") is not None:
                return f"{s.get('id_type')}:{s.get('id')}"
            return s.get("ref_id") or s.get("pmid")
        literature_sources = [s for s in sources if s.get("id_type") in ("PMID", "PMCID")]
        official_sources = [s for s in sources if s.get("id_type") == "URL"]
        if literature_sources:
            st.markdown("**Literature (PubMed/PMC)**")
            df_lit = pd.DataFrame(
                [{"id": _source_id(s), "title": s.get("title"), "year": s.get("year"), "url": s.get("url")}
                for s in literature_sources]
            )
            st.dataframe(df_lit, use_container_width=True)
        if official_sources:
            st.markdown("**Official / Regulatory**")
            df_off = pd.DataFrame(
                [{"id": _source_id(s), "title": s.get("title"), "url": s.get("url")}
                for s in official_sources]
            )
            st.dataframe(df_off, use_container_width=True)
        pmids = [_source_id(s) for s in sources]
        if "selected_sources" not in st.session_state:
            st.session_state["selected_sources"] = pmids
        col_src1, col_src2 = st.columns([3, 1])
        with col_src2:
            if st.button("Снять все", key="deselect_all_sources"):
                st.session_state["selected_sources"] = []
            if st.button("Выбрать все", key="select_all_sources"):
                st.session_state["selected_sources"] = pmids
        with col_src1:
            st.multiselect(
                "Выберите источники",
                options=pmids,
                key="selected_sources",
            )


fullreport = st.session_state.get("fullreport")
pk_state = st.session_state.get("pk")

cv_source, cv_value, cv_evidence, cv_info = _resolve_cv_context(fullreport, pk_state)
ci_values = _as_list((fullreport or {}).get("ci_values") or (pk_state or {}).get("ci_values"))
dq_level = _get((fullreport or {}).get("data_quality"), "level")
cv_extracted_value = cv_value

st.markdown("## Подтверждение CVintra (желательно для финализации)")
st.caption("Подтверждение желательно для финализации; без подтверждения N_det может быть рассчитан по eligible CV при Run pipeline, но будет помечен как provisional.")
st.markdown(f"**Источник CV:** `{cv_source}`")

cv_confirmed_checked = st.checkbox(
    "Подтверждаю: значение CVintra корректно (для финализации расчёта N_det)",
    key="cv_confirmed_checkbox",
    value=bool(st.session_state.get("cv_confirmed", False)),
)
cv_value = st.session_state.get("manual_cv")
if cv_confirmed_checked and cv_value and float(cv_value) > 0:
    st.session_state["cv_confirmed"] = True
else:
    st.session_state["cv_confirmed"] = False
    if cv_confirmed_checked and not cv_value:
        st.warning("Введите значение CV перед подтверждением.")
cv_confirmed = bool(st.session_state.get("cv_confirmed", False))

if cv_extracted_value is not None:
    try:
        cv_display = f"{float(cv_extracted_value):.1f}%"
    except (TypeError, ValueError):
        cv_display = str(cv_extracted_value)
    st.metric("CVintra (%)", value=cv_display)
else:
    st.info("CVintra пока недоступен. Можно ввести значение вручную ниже.")

if cv_source == "derived_from_ci":
    ci_low, ci_high, ci_n = _find_ci_for_cv(ci_values)
    st.info(
        "Допущения для расчёта CV по ДИ: 90% ДИ, 2×2 кроссовер, лог-шкала. "
        f"CI_low={ci_low or '—'}, CI_high={ci_high or '—'}, n={ci_n or '—'}"
    )

_render_evidence(cv_evidence)

show_manual = cv_extracted_value is None or cv_source in ("range", "unknown") or dq_level == "red"
manual_cv_value = None
if show_manual:
    st.caption("Ручное значение CV также требует подтверждения (галочка выше).")
    use_manual_cv = st.checkbox("Задать CVintra вручную", value=True, key="use_manual_cv")
    if use_manual_cv:
        st.caption("Предполагаемая внутрисубъектная вариабельность: ориентиры — низкая (~20%), высокая (~40%). Либо укажите точное значение ниже.")
        preset_cols = st.columns(4)
        presets = [20, 30, 40, 50]
        for i, p in enumerate(presets):
            if preset_cols[i].button(f"{p}%"):
                st.session_state["manual_cv"] = p
                st.session_state["manual_cv_input"] = p
        if "manual_cv_input" not in st.session_state:
            st.session_state["manual_cv_input"] = st.session_state.get("manual_cv", 30)
        manual_default = st.session_state.get("manual_cv_input", 30)
        manual_cv_value = st.number_input(
            "CVintra (%)",
            value=float(manual_default),
            min_value=1.0,
            max_value=200.0,
            key="manual_cv_input",
        )
        if manual_cv_value and manual_cv_value > 0:
            st.session_state["manual_cv"] = float(manual_cv_value)

st.markdown("---")
st.subheader("4) Параметры расчёта (Шаг 4 — можно оставить по умолчанию)")
st.slider("Мощность (power)", 0.5, 0.99, 0.8, key="power")
st.slider("Уровень значимости (alpha)", 0.01, 0.1, 0.05, key="alpha")
st.slider("Доля выбываний (dropout)", 0.0, 0.5, 0.2, key="dropout")
st.slider("Доля screen-fail", 0.0, 0.8, 0.2, key="screen_fail")

st.subheader("5) Регуляторный ввод (Шаг 5 — опционально)")
st.number_input("Длительность вымывания (дни)", value=0.0, min_value=0.0, key="schedule_days")
with st.expander("Дополнительные параметры (опционально)"):
    st.number_input("Длительность госпитализации (дни)", value=0.0, min_value=0.0, key="hospitalization_duration_days")
    st.number_input("Длительность забора проб (дни)", value=0.0, min_value=0.0, key="sampling_duration_days")
    st.number_input("Длительность наблюдения (дни)", value=0.0, min_value=0.0, key="follow_up_duration_days")
    phone_follow_up_label = st.selectbox(
        "Допустим ли телефонный follow-up?",
        ["не указано", "Да", "Нет"],
        index=0,
        key="phone_follow_up_label",
    )
    phone_follow_up_ok = None
    if phone_follow_up_label == "Да":
        phone_follow_up_ok = True
    elif phone_follow_up_label == "Нет":
        phone_follow_up_ok = False
    st.session_state["phone_follow_up_ok"] = phone_follow_up_ok
    st.number_input("Общий объём крови (мл)", value=0.0, min_value=0.0, key="blood_volume_total_ml")
    st.number_input("Объём крови только для PK (мл)", value=0.0, min_value=0.0, key="blood_volume_pk_ml")

st.markdown("---")
st.subheader("▶ Запуск полного расчёта (Run pipeline)")
st.caption("**Run pipeline делает всё.** Кнопки ниже («Найти источники», «Извлечь PK» и т.д.) — для дебага и пошагового режима.")


# ── Валидация перед запуском ───────────────────────────────────────────────
def _validate_inputs() -> list[str]:
    errors = []
    if not inn_ru:
        errors.append("Введите МНН препарата")
    if not inn_en:
        errors.append("Определите английский INN (нажмите «🔄 Определить INN EN»)")
    if not (dosage_form or "").strip():
        errors.append("Укажите лекарственную форму")
    if not (st.session_state.get("dose") or "").strip():
        errors.append("Укажите дозировку")
    return errors


validation_errors = _validate_inputs()
if validation_errors:
    for err in validation_errors:
        st.error(f"• {err}")
    run_disabled = True
else:
    run_disabled = False
    if st.session_state.get("protocol_condition") is None:
        st.warning("Режим приёма «Не знаю» — расчёт может использовать допущения.")

# Метаданные для payload (заполняются в блоке Advanced ниже или дефолты)
protocol_id = st.session_state.get("protocol_id", "")
protocol_status = "Черновик" if not (protocol_id or "").strip() else "Финальный"
replacement_subjects = st.session_state.get("replacement_subjects_label", "Нет") == "Да"
visit_day_numbering = st.session_state.get("visit_day_numbering", "continuous across periods")
study_phase = STUDY_PHASE_RU_TO_API.get(
    st.session_state.get("study_phase_label", "автовыбор моделью"),
    None,
)
gender_requirement = st.session_state.get("gender_requirement") or None
age_range = (st.session_state.get("age_range") or "").strip() or None
additional_constraints = (st.session_state.get("additional_constraints") or "").strip() or None

if st.button(
    "▶ Запустить полный расчёт (Run pipeline)",
    type="primary",
    disabled=run_disabled,
):
    seed_val = st.session_state.get("risk_seed")
    if seed_val == 0:
        seed_val = None
    risk_dist = st.session_state.get("risk_distribution") or None
    payload = {
        "inn": inn_en or inn_ru,
        "inn_ru": inn_ru or None,
        "dosage_form": (st.session_state.get("dosage_form") or "").strip() or None,
        "dose": (st.session_state.get("dose") or "").strip() or None,
        "retmax": 10,
        "selected_sources": st.session_state.get("selected_sources") or None,
        "manual_cv": st.session_state.get("manual_cv"),
        "cv_confirmed": st.session_state.get("cv_confirmed", False),
        "rsabe_requested": st.session_state.get("rsabe_requested") or None,
        "preferred_design": (st.session_state.get("preferred_design") or None),
        "power": float(st.session_state.get("power", 0.8)),
        "alpha": float(st.session_state.get("alpha", 0.05)),
        "dropout": float(st.session_state.get("dropout", 0.1)),
        "screen_fail": float(st.session_state.get("screen_fail", 0.1)),
        "risk_seed": seed_val,
        "risk_n_sims": int(st.session_state.get("risk_n_sims", 5000)),
        "risk_distribution": risk_dist,
        "protocol_id": protocol_id if protocol_id.strip() else None,
        "replacement_subjects": replacement_subjects,
        "visit_day_numbering": visit_day_numbering,
        "protocol_condition": st.session_state.get("protocol_condition"),
        "nti": st.session_state.get("nti"),
        "study_phase": study_phase,
        "schedule_days": st.session_state.get("schedule_days") or None,
        "hospitalization_duration_days": st.session_state.get("hospitalization_duration_days") or None,
        "sampling_duration_days": st.session_state.get("sampling_duration_days") or None,
        "follow_up_duration_days": st.session_state.get("follow_up_duration_days") or None,
        "phone_follow_up_ok": st.session_state.get("phone_follow_up_ok"),
        "blood_volume_total_ml": st.session_state.get("blood_volume_total_ml") or None,
        "blood_volume_pk_ml": st.session_state.get("blood_volume_pk_ml") or None,
        "gender_requirement": gender_requirement or None,
        "age_range": (age_range or "").strip() or None,
        "additional_constraints": (additional_constraints or "").strip() or None,
    }
    try:
        resp = api_post("/run_pipeline", payload)
        st.session_state["fullreport"] = resp
        st.success("Расчёт завершён.")
    except Exception as exc:
        st.error(f"Ошибка pipeline: {exc}")


with st.expander("Advanced / Общие настройки протокола", expanded=False):
    st.caption("**Не требуется для Run pipeline.** Заполняется только при финализации протокола или для экспорта в документ.")
    st.text_input("Идентификатор протокола (необязательно)", value="", key="protocol_id")
    st.selectbox(
        "Резервные испытуемые (замена выбывших)",
        ["Нет", "Да"],
        index=0,
        key="replacement_subjects_label",
    )
    st.text_input(
        "Нумерация визитов/дней",
        value="continuous across periods",
        key="visit_day_numbering",
        help="Например: continuous across periods",
    )
    st.selectbox(
        "Тип исследования",
        STUDY_PHASE_OPTIONS_RU,
        index=0,
        key="study_phase_label",
        help="Однопериодное / двухпериодное (БЭ) или автовыбор моделью",
    )
    with st.expander("Дополнительные требования заказчика", expanded=False):
        col_cro1, col_cro2 = st.columns(2)
        with col_cro1:
            gender_requirement = st.selectbox(
                "Гендерный состав",
                ["", "мужчины и женщины", "только мужчины", "только женщины"],
                index=0,
                key="gender_requirement",
            )
        with col_cro2:
            age_range = st.text_input(
                "Возрастной диапазон",
                value=st.session_state.get("age_range", "18-45"),
                key="age_range",
                help="Например: 18-55, 18-65",
            )
        col_bmi1, col_bmi2 = st.columns(2)
        with col_bmi1:
            st.number_input("BMI min", value=18.5, min_value=10.0, max_value=40.0, step=0.1, key="bmi_min")
        with col_bmi2:
            st.number_input("BMI max", value=30.0, min_value=10.0, max_value=60.0, step=0.1, key="bmi_max")
        center_name = st.text_input("Центр проведения", value="TBD", key="center_name")
        lab_name = st.text_input("Биоаналитическая лаборатория", value="TBD", key="lab_name")
        sponsor_name = st.text_input("Спонсор/заказчик", value="TBD", key="sponsor_name")
        safety_default = (
            "ECG, витальные показатели, лаборатория (гем/биох/моча), регистрация AE/SAE. "
            "Проводить до приема каждого препарата (в каждом периоде) и в протокольные временные точки после приема."
        )
        st.text_area(
            "Процедуры безопасности",
            value=safety_default,
            key="safety_procedures",
        )
        st.text_area(
            "Иные ограничения заказчика",
            value="",
            key="additional_constraints",
            help="Любые дополнительные требования к дизайну исследования",
        )


st.subheader("2) Вариабельность и ключевые PK")
selected_sources = st.session_state.get("selected_sources", [])
if st.button("Извлечь PK"):
    try:
        resp = api_post("/extract_pk", {
            "inn": inn_en or inn_ru,
            "inn_ru": inn_ru or None,
            "sources": selected_sources,
        })
        st.session_state["pk"] = resp
        st.success("PK данные извлечены")
    except Exception as exc:
        st.error(f"Ошибка извлечения: {exc}")

pk = st.session_state.get("pk")
pk_values_display = _as_list((st.session_state.get("fullreport") or {}).get("pk_values") or (pk or {}).get("pk_values"))
study_condition = (st.session_state.get("fullreport") or {}).get("study_condition") or (pk or {}).get(
    "study_condition"
)
meal_details = (st.session_state.get("fullreport") or {}).get("meal_details") or (pk or {}).get("meal_details") or {}
pk_warnings = (st.session_state.get("fullreport") or {}).get("warnings") or (pk or {}).get("warnings") or []
ci_values_display = _as_list((st.session_state.get("fullreport") or {}).get("ci_values") or (pk or {}).get("ci_values"))
data_quality_flags = {
    "be_tables_found": any("regex_fallback_cv" in w or "ci_present_but_not_extracted" in w for w in pk_warnings),
    "supplementary_possible": any("data_may_be_in_supplementary" in w for w in pk_warnings),
    "feeding_conflict": any("feeding_condition_conflict" in w for w in pk_warnings),
}
if pk_values_display:
    pk_rows = []
    for pkv in pk_values_display:
        ev = (pkv.get("evidence") or [{}])[0]
        source_ref = ev.get("pmid_or_url") or ev.get("pmid") or ev.get("url") or ev.get("source_id") or ev.get("source")
        snippet = ev.get("excerpt") or ev.get("snippet")
        pk_rows.append(
            {
                "metric": pkv.get("name"),
                "value": pkv.get("value"),
                "unit": pkv.get("unit"),
                "source": source_ref,
                "snippet": snippet,
            }
        )
    st.dataframe(pd.DataFrame(pk_rows), use_container_width=True)
    if pk_warnings:
        st.warning("; ".join(pk_warnings))
    if pk and pk.get("validation_issues"):
        st.warning(f"Замечания валидации: {pk.get('validation_issues')}")
    if study_condition:
        st.caption(f"Условие исследования: {study_condition}")
    if meal_details:
        details_text = ", ".join(
            [f"{key}={value}" for key, value in meal_details.items() if value not in (None, "")]
        )
        if details_text:
            st.caption(f"Детали приёма пищи: {details_text}")

# Флаги качества/источники
flag_cols = st.columns(3)
flag_cols[0].metric("BE-таблицы (CI+CV)", "Да" if data_quality_flags["be_tables_found"] else "—")
flag_cols[1].metric("Supplementary?", "Да" if data_quality_flags["supplementary_possible"] else "—")
flag_cols[2].metric("Конфликт fed/fasted", "Есть" if data_quality_flags["feeding_conflict"] else "—")


st.subheader("3) Дизайн исследования")
nti_flag = st.session_state.get("nti")
design_resp = st.session_state.get("design")
design_from_report = _format_design(st.session_state.get("fullreport"), design_resp)
recommended_design = design_from_report.get("design") or design_from_report.get("recommendation") or "2x2_crossover"
reasoning_text = design_from_report.get("reasoning_text") or ""
pk_payload = pk
if not pk_payload and st.session_state.get("fullreport"):
    fullreport_pk = (st.session_state.get("fullreport") or {}).get("pk_values")
    if fullreport_pk is not None:
        pk_payload = {
            "inn": inn_en or inn_ru,
            "pk_values": fullreport_pk or [],
            "ci_values": (st.session_state.get("fullreport") or {}).get("ci_values") or [],
            "warnings": [],
            "missing": [],
            "validation_issues": [],
        }

design_clicked = st.button("Подобрать дизайн")
if design_clicked and pk_payload:
    cv_payload = None
    cv_payload_value = manual_cv_value if manual_cv_value is not None else cv_extracted_value
    if cv_payload_value is not None:
        cv_payload = {
            "cv": {
                "value": float(cv_payload_value),
                "unit": "%",
                "evidence": [
                    {
                        "source_type": "URL",
                        "source": "manual://user",
                        "snippet": "User input",
                        "context": "Manual CV input",
                    }
                ],
            },
            "confirmed": bool(cv_confirmed),
        }
    try:
        resp = api_post("/select_design", {"pk_json": pk_payload, "cv_input": cv_payload, "nti": nti_flag})
        design_value = resp.get("recommendation") or resp.get("design")
        if not design_value:
            st.session_state["design"] = None
            st.error(
                "**Определение дизайна невозможно.** Ответ API не содержит recommendation/design "
                "(например, сервис LLM недоступен). Для высоковариабельных препаратов (CV > 30%) или длинного T½ "
                "подстановка 2×2 кроссовера недопустима. Выберите дизайн вручную в блоке «Предпочтительный дизайн» "
                "и повторите Run pipeline, либо обратитесь к разработчику."
            )
            design_from_report = {}
        else:
            st.session_state["design"] = design_value
            st.success("Дизайн подобран")
            design_from_report = _format_design(st.session_state.get("fullreport"), resp)
    except Exception as exc:
        st.session_state["design"] = None
        st.error(f"Ошибка дизайна: {exc}")
elif design_clicked and not pk_payload:
    st.warning("Нет PK данных для выбора дизайна. Запустите pipeline или извлеките PK.")

# Выбор дизайна (авто/ручной)
options_design = [
    (f"Авто (рекомендовано: {recommended_design})", None),
    ("2×2 crossover", "2x2_crossover"),
    ("3-way replicate", "3-way_replicate"),
    ("4-way replicate", "4-way_replicate"),
    ("parallel", "parallel"),
]
labels = [lbl for lbl, _ in options_design]
sel_label = st.selectbox("Рекомендованный дизайн (можно изменить)", labels, index=0, key="preferred_design_choice")
preferred_design = next((val for lbl, val in options_design if lbl == sel_label), None)
st.session_state["preferred_design"] = preferred_design

col_des1, col_des2 = st.columns([3, 1])
with col_des1:
    if reasoning_text:
        st.info(f"Обоснование дизайна: {reasoning_text}")
    elif design_from_report:
        st.info(f"Дизайн: {recommended_design}")
with col_des2:
    st.session_state["rsabe_requested"] = st.checkbox(
        "Рассмотреть RSABE (если HVD)",
        value=st.session_state.get("rsabe_requested", False),
        help="Вкл. принудительно выберет replicate, если CV высокий.",
    )

with st.expander("Рандомизация (по умолчанию)", expanded=False):
    st.caption("1:1, последовательности TR/RT, блочная рандомизация.")

with st.expander("Отмывка (Advanced)", expanded=False):
    wash_mult = st.number_input(
        "Коэффициент отмывки, × t1/2",
        min_value=1.0,
        max_value=10.0,
        value=float(st.session_state.get("washout_multiplier", 5.0)),
        step=0.5,
        key="washout_multiplier",
        help="Используется как рекомендация; по умолчанию 5× t1/2.",
    )


st.subheader("4) Оценка вариабельности (опционально)")
colA, colB, colC = st.columns(3)
with colA:
    bcs_class = st.selectbox("Класс BCS", [None, 1, 2, 3, 4], index=0)
with colB:
    logp = st.number_input("logP", value=0.0, min_value=-10.0, max_value=10.0,
                       help="Коэффициент липофильности. Может быть отрицательным.")
with colC:
    first_pass = st.selectbox("First-pass метаболизм", [None, "low", "medium", "high"], index=0)

colD, colE = st.columns(2)
with colD:
    cyp = st.selectbox("Участие CYP", [None, "low", "medium", "high"], index=0)
with colE:
    nti_var = st.checkbox("NTI", value=False, key="nti_var")

if st.button("Оценить CV диапазон"):
    try:
        resp = api_post(
            "/variability_estimate",
            {
                "inn": inn_en or inn_ru,
                "bcs_class": bcs_class,
                "logp": logp if logp > 0 else None,
                "first_pass": first_pass,
                "cyp_involvement": cyp,
                "nti": nti_var,
                "pk_json": pk,
            },
        )
        st.session_state["variability"] = resp
        st.success("Диапазон CV рассчитан")
    except Exception as exc:
        st.error(f"Ошибка вариабельности: {exc}")

if st.session_state.get("variability"):
    st.write(st.session_state["variability"])


st.subheader("5) Размер выборки (просмотр результатов)")
no_replacement = st.checkbox("Не заменять выбывших", value=False, key="no_replacement")

det_tab, risk_tab = st.tabs(["Детерминированный (N_det)", "С учётом риска (N_risk)"])

with det_tab:
    if not cv_confirmed:
        st.info("Для ручного расчёта N_det здесь требуется подтверждение CV выше (галочка «Подтверждаю»). Run pipeline считает N_det и без подтверждения, если CV eligible.")

    sample_det = (st.session_state.get("fullreport") or {}).get("sample_size_det")
    if sample_det:
        st.write(sample_det)
    else:
        st.caption("N_det не рассчитан или помечен как provisional (при Run pipeline может считаться по eligible CV без подтверждения).")

    if st.button("Рассчитать N_det", disabled=not cv_confirmed):
        design_value = design_from_report.get("design") if design_from_report else None
        cv_for_calc = manual_cv_value if manual_cv_value is not None else cv_extracted_value
        if not design_value:
            st.warning("Дизайн не определён.")
        elif cv_for_calc is None:
            st.warning("Не задано значение CVintra.")
        else:
            try:
                resp = api_post(
                    "/calc_sample_size",
                    {
                        "design": design_value,
                        "cv_input": {
                            "cv": {
                                "value": float(cv_for_calc),
                                "unit": "%",
                                "evidence": [
                                    {
                                        "source_type": "URL",
                                        "source": "manual://user",
                                        "snippet": "User input",
                                        "context": "Manual CV input",
                                    }
                                ],
                            },
                            "confirmed": bool(cv_confirmed),
                        },
                       "power": float(st.session_state.get("power", 0.8)),
                       "alpha": float(st.session_state.get("alpha", 0.05)),
                        "dropout": float(st.session_state.get("dropout", 0.2)),
                        "screen_fail": float(st.session_state.get("screen_fail", 0.2)),
                    },
                )
                st.session_state["sample"] = resp
                st.success("N_det рассчитан")
                st.write(resp)
                st.caption(
                    f"N_analysis={resp.get('N_total',{}).get('value')}; "
                    f"N_rand={resp.get('N_rand',{}).get('value')}; "
                    f"N_screen={resp.get('N_screen',{}).get('value')}"
                )
            except Exception as exc:
                st.error(f"Ошибка расчета N_det: {exc}")

with risk_tab:
    st.number_input("Seed для симуляций (необязательно)", value=0, min_value=0, key="risk_seed")
    st.number_input("Число симуляций Монте-Карло", value=5000, min_value=1000, max_value=50000, key="risk_n_sims")
    st.text_input("Распределение CV (необязательно)", value="", key="risk_distribution")

    sample_risk = (st.session_state.get("fullreport") or {}).get("sample_size_risk")
    if sample_risk:
        targets = sample_risk.get("n_targets") or {}
        p_success = sample_risk.get("p_success_at_n") or {}
        rows = []
        for key in ["0.7", "0.8", "0.9"]:
            rows.append(
                {
                    "Psuccess": key,
                    "N_target": targets.get(key),
                    "Psuccess@N": p_success.get(key),
                }
            )
        st.table(pd.DataFrame(rows))
        st.caption(
            f"seed={sample_risk.get('seed')}, n_sims={sample_risk.get('n_sims')}, rng={sample_risk.get('rng_name')}"
        )
        st.caption(f"method={sample_risk.get('method')}, numpy={sample_risk.get('numpy_version')}")
    else:
        st.caption("N_risk не рассчитан (требуется диапазон/распределение CV).")


st.subheader("6) Качество данных и регуляторная проверка")
data_quality = (st.session_state.get("fullreport") or {}).get("data_quality")
if data_quality:
    st.metric("Индекс качества данных (DQI)", value=str(data_quality.get("score", "—")))
    components = data_quality.get("components") or {}
    traceability = components.get("traceability")
    if traceability is not None:
        try:
            st.caption(f"Компонент прослеживаемости: {float(traceability):.2f}")
        except Exception:
            st.caption(f"Компонент прослеживаемости: {traceability}")
    st.write(data_quality)
else:
    st.info("Качество данных: не рассчитано.")

reg_checks = (st.session_state.get("fullreport") or {}).get("reg_check") or (st.session_state.get("reg") or {}).get("checks")
open_questions = (st.session_state.get("fullreport") or {}).get("open_questions") or (st.session_state.get("reg") or {}).get(
    "open_questions"
)

if reg_checks:
    st.write(reg_checks)
else:
    st.caption("Регуляторная проверка: пунктов нет.")

if open_questions:
    st.subheader("Открытые вопросы / Требуют уточнения")
    for item in open_questions:
        st.write(f"- {item.get('question')} (приоритет: {item.get('priority')})")
else:
    st.caption("Открытых вопросов нет.")


st.subheader("7) Регуляторный ввод (результаты чек-листа)")
st.caption("Параметры (washout, длительности, объём крови) задаются в блоке «5) Регуляторный ввод» выше перед Run pipeline.")

if st.session_state.get("fullreport"):
    st.success("✅ Регуляторный чек-лист выполнен в рамках Run pipeline — результаты в секции 6 выше.")
elif pk:
    design = st.session_state.get("design")
    if st.button("Проверить чек-лист (ручной режим)"):
        if not design:
            st.warning("⚠️ Дизайн не определён. Сначала нажмите 'Подобрать дизайн' в секции 3.")
        else:
            cv_payload = None
            cv_payload_value = manual_cv_value if manual_cv_value is not None else cv_extracted_value
            if cv_payload_value is not None:
                cv_payload = {
                    "cv": {
                        "value": float(cv_payload_value),
                        "unit": "%",
                        "evidence": [{"source_type": "URL", "source": "manual://user",
                                      "snippet": "User input", "context": "Manual CV input"}],
                    },
                    "confirmed": bool(cv_confirmed),
                }
            try:
                resp = api_post(
                    "/reg_check",
                    {
                        "design": design,
                        "pk_json": pk,
                        "schedule_days": st.session_state.get("schedule_days") or None,
                        "cv_input": cv_payload,
                        "hospitalization_duration_days": st.session_state.get("hospitalization_duration_days") or None,
                        "sampling_duration_days": st.session_state.get("sampling_duration_days") or None,
                        "follow_up_duration_days": st.session_state.get("follow_up_duration_days") or None,
                        "phone_follow_up_ok": st.session_state.get("phone_follow_up_ok"),
                        "blood_volume_total_ml": st.session_state.get("blood_volume_total_ml") or None,
                        "blood_volume_pk_ml": st.session_state.get("blood_volume_pk_ml") or None,
                    },
                )
                st.session_state["reg"] = resp
                st.success("Чек-лист готов")
            except Exception as exc:
                st.error(f"Ошибка чек-листа: {exc}")
else:
    st.info("ℹ️ Регуляторный чек-лист запускается автоматически при нажатии ▶ Run pipeline.")


def _build_markdown_synopsis(report: dict) -> str:
    study = report.get("study") or {}
    design_obj = report.get("design") or study.get("design") or {}
    dq = report.get("dqi") or report.get("data_quality") or {}
    inn_display = report.get("inn_ru") or report.get("inn", "—")
    lines = [
        f"# Синопсис протокола исследования биоэквивалентности",
        "",
        f"**Действующее вещество (МНН):** {inn_display}",
        f"**Лекарственная форма:** {report.get('dosage_form') or '—'}",
        f"**Дозировка:** {report.get('dose') or '—'}",
        f"**Номер протокола:** {report.get('protocol_id') or '—'}",
        f"**Статус:** {('Черновик' if (report.get('protocol_status') or '') == 'Draft' else 'Финальный' if (report.get('protocol_status') or '') == 'Final' else report.get('protocol_status') or '—')}",
        "",
        "## Цель исследования",
        f"Оценка биоэквивалентности тестового и референтного препаратов "
        f"действующего вещества {inn_display} у здоровых добровольцев.",
        "",
        "## Задачи исследования",
        "1. Определить фармакокинетические параметры (Cmax, AUC0-t, AUC0-inf).",
        "2. Провести статистическое сравнение PK-параметров.",
        "3. Оценить безопасность и переносимость.",
        "",
        "## Дизайн исследования",
    ]
    rec = (design_obj.get("recommendation") or design_obj.get("recommended")
           or design_obj.get("design") or "—")
    lines.append(f"- **Рекомендованный дизайн:** {rec}")
    _cond = report.get("protocol_condition")
    _cond_ru = PROTOCOL_CONDITION_API_TO_RU.get(_cond, _cond or "—")
    lines.append(f"- **Режим приёма:** {_cond_ru}")
    _phase = report.get("study_phase")
    _phase_ru = {"single": "однопериодное", "two-phase": "двухпериодное", "auto": "автовыбор"}.get(_phase, _phase or "—")
    lines.append(f"- **Тип исследования:** {_phase_ru}")
    lines.append("")
    lines.append("## Обоснование дизайна")
    reasoning = design_obj.get("reasoning_text") or design_obj.get("reasoning") or "—"
    if isinstance(reasoning, list):
        reasoning = "; ".join(str(r) for r in reasoning)
    lines.append(reasoning)
    lines.append("")
    lines.append("## Исследуемая популяция")
    lines.append(f"- **Пол:** {report.get('gender_requirement') or '—'}")
    lines.append(f"- **Возраст:** {report.get('age_range') or '—'}")
    if report.get("additional_constraints"):
        lines.append(f"- **Ограничения:** {report['additional_constraints']}")
    lines.append("")
    lines.append("## Первичные конечные точки")
    lines.append("Cmax, AUC0-t (90% ДИ отношения геометрических средних: 80.00–125.00%).")
    lines.append("")
    lines.append("## Фармакокинетические параметры")
    pk_vals = report.get("pk_values") or []
    if pk_vals:
        lines.append("| Параметр | Значение | Единицы |")
        lines.append("|---|---|---|")
        for pk in pk_vals:
            n = pk.get("name", "—")
            v = pk.get("value", "—")
            u = pk.get("unit", "—")
            lines.append(f"| {n} | {v} | {u} |")
    else:
        lines.append("Данные не извлечены.")
    lines.append("")
    lines.append("## Размер выборки")
    sdet = report.get("sample_size_det") or {}
    if sdet.get("n_total"):
        lines.append(f"- N_det (total): {sdet['n_total']}, rand: {sdet.get('n_rand', '—')}, screen: {sdet.get('n_screen', '—')}")
        lines.append(f"- CV: {sdet.get('cv', '—')}%, power: {sdet.get('power', '—')}, alpha: {sdet.get('alpha', '—')}")
    else:
        lines.append("N_det не рассчитан или помечен как provisional (при расчёте без подтверждения CV).")
    lines.append("")
    lines.append("## Статистические методы")
    lines.append("ANOVA логарифмически преобразованных PK-параметров. 90% ДИ для Test/Reference. Критерий: 80.00–125.00%.")
    lines.append("")
    lines.append("## План мониторинга безопасности")
    safety_plan = report.get("safety_procedures") or (
        "Контроль безопасности у здоровых добровольцев включает лабораторные анализы крови и мочи, "
        "витальные показатели (частота сердечных сокращений, частота дыхания, артериальное давление), "
        "регистрацию ЭКГ, а также мониторинг НЯ/СНЯ. "
        "Оценки выполняются до приема каждого препарата (преддоза) и в определенные протоколом исследования "
        "временные точки после приема, а также при выписке/на визите завершения периода и в период наблюдения."
    )
    lines.append(safety_plan if isinstance(safety_plan, str) else str(safety_plan))
    lines.append("")
    lines.append("## Качество данных (DQI)")
    lines.append(f"- Score: {dq.get('score', '—')}, Level: {dq.get('level', '—')}")
    for r in (dq.get("reasons") or [])[:3]:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Регуляторные замечания / Open Questions")
    oq = report.get("open_questions") or []
    if oq:
        for q in oq:
            txt = q.get("question") if isinstance(q, dict) else str(q)
            lines.append(f"- {txt}")
    else:
        lines.append("Нет открытых вопросов.")
    lines.append("")
    lines.append("## Библиографический список источников")
    sources = report.get("sources") or []
    if sources:
        for i, s in enumerate(sources, 1):
            id_type, id_val = s.get("id_type"), s.get("id")
            if id_type and id_val is not None:
                ref_id = f"{id_type}:{id_val}"
            else:
                ref_id = s.get("ref_id") or (f"PMCID:{s.get('pmcid')}" if s.get("pmcid") else f"PMID:{s.get('pmid', '—')}")
            title = s.get("title", "—")
            year = s.get("year", "—")
            lines.append(f"{i}. {title} ({year}) {ref_id}")
    else:
        lines.append("Источники не определены.")
    lines.append("")
    return "\n".join(lines)


st.subheader("8) Экспорт")
fullreport_export = st.session_state.get("fullreport") or {
    "inn": inn_en or inn_ru,
    "inn_ru": inn_ru or None,
    "dosage_form": dosage_form.strip() or None,
    "dose": (st.session_state.get("dose") or "").strip() or None,
    "protocol_id": (protocol_id or "").strip() or None,
    "protocol_status": protocol_status,
    "replacement_subjects": replacement_subjects,
    "visit_day_numbering": visit_day_numbering,
    "protocol_condition": st.session_state.get("protocol_condition"),
    "study_phase": study_phase,
    "gender_requirement": gender_requirement or None,
    "age_range": (age_range or "").strip() or None,
    "additional_constraints": (additional_constraints or "").strip() or None,
    "schedule_days": st.session_state.get("schedule_days") or None,
    "hospitalization_duration_days": st.session_state.get("hospitalization_duration_days") or None,
    "sampling_duration_days": st.session_state.get("sampling_duration_days") or None,
    "follow_up_duration_days": st.session_state.get("follow_up_duration_days") or None,
    "phone_follow_up_ok": st.session_state.get("phone_follow_up_ok"),
    "blood_volume_total_ml": st.session_state.get("blood_volume_total_ml") or None,
    "blood_volume_pk_ml": st.session_state.get("blood_volume_pk_ml") or None,
    "sources": st.session_state.get("sources", []),
    "pk_values": (st.session_state.get("pk") or {}).get("pk_values", []),
    "ci_values": (st.session_state.get("pk") or {}).get("ci_values", []),
    "study_condition": (st.session_state.get("pk") or {}).get("study_condition"),
    "meal_details": (st.session_state.get("pk") or {}).get("meal_details"),
    "design_hints": (st.session_state.get("pk") or {}).get("design_hints"),
    "design": st.session_state.get("design"),
    "sample_size_det": st.session_state.get("sample"),
    "sample_size_risk": (st.session_state.get("fullreport") or {}).get("sample_size_risk"),
    "reg_check": (st.session_state.get("reg") or {}).get("checks", []),
    "open_questions": (st.session_state.get("reg") or {}).get("open_questions", []),
    "safety_procedures": st.session_state.get("safety_procedures"),
}

json_blob = json.dumps(fullreport_export, ensure_ascii=False, indent=2)

export_col1, export_col2, export_col3 = st.columns(3)
with export_col1:
    st.download_button(
        "Скачать FullReport.json",
        data=json_blob,
        file_name="FullReport.json",
        mime="application/json",
    )
with export_col2:
    md_text = _build_markdown_synopsis(fullreport_export)
    st.download_button(
        "Скачать synopsis.md",
        data=md_text,
        file_name="synopsis.md",
        mime="text/markdown",
    )

with export_col3:
    pass

if st.button("Собрать синопсис .docx"):
    try:
        resp = api_post("/build_docx", {"all_json": fullreport_export})
        if resp.get("warnings"):
            st.error("Ошибка формирования docx. См. предупреждения.")
            st.write(resp.get("warnings"))
            st.session_state["docx_error"] = resp.get("warnings")
            st.session_state["docx_bytes"] = None
            st.session_state["docx_filename"] = None
        else:
            path = resp.get("path_to_docx")
            if not path:
                st.error("Docx render failed: no file path returned.")
                st.session_state["docx_error"] = ["no_docx_path"]
                st.session_state["docx_bytes"] = None
                st.session_state["docx_filename"] = None
            else:
                try:
                    with open(path, "rb") as f:
                        st.session_state["docx_bytes"] = f.read()
                    st.session_state["docx_filename"] = os.path.basename(path) or "synopsis.docx"
                    st.session_state["docx_error"] = None
                    st.success("Docx создан. Нажмите кнопку скачивания ниже.")
                except Exception as exc:
                    st.error(f"Не удалось прочитать docx файл: {exc}")
                    st.session_state["docx_error"] = [str(exc)]
                    st.session_state["docx_bytes"] = None
                    st.session_state["docx_filename"] = None
    except Exception as exc:
        st.error(f"Ошибка docx: {exc}")
        st.session_state["docx_error"] = [str(exc)]
        st.session_state["docx_bytes"] = None
        st.session_state["docx_filename"] = None

if st.session_state.get("docx_bytes"):
    st.download_button(
        "Скачать synopsis.docx",
        data=st.session_state["docx_bytes"],
        file_name=st.session_state.get("docx_filename") or "synopsis.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

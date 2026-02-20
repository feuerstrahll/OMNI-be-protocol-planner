import json
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="BE Planning MVP", layout="wide")
st.title("BE Planning MVP")


@st.cache_data(show_spinner=False)
def api_post(path: str, payload: dict) -> dict:
    resp = requests.post(f"{BACKEND_URL}{path}", json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(resp.text)
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
        st.caption("Evidence not available.")
        return
    for ev in evidence_list:
        excerpt = ev.get("excerpt") or ev.get("snippet") or "Evidence not available."
        source = ev.get("pmid_or_url") or ev.get("pmid") or ev.get("url") or ev.get("source")
        pmid = ev.get("pmid")
        if not pmid and isinstance(source, str) and source.isdigit():
            pmid = source
        st.caption(excerpt)
        if pmid:
            st.markdown(f"Source: PMID [{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)")
        elif source:
            st.caption(f"Source: {source}")


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
    st.session_state["cv_confirmed"] = False
    st.session_state["manual_cv"] = None


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


with st.expander("📋 Порядок работы с системой", expanded=False):
    st.markdown(
        """
**Шаг 1 — Заполните метаданные** (секция 0): форма, доза, режим приёма, пол, возраст

**Шаг 2 — Введите INN** (секция 1): название препарата. Опционально: найдите источники в PubMed и оставьте только релевантные (BE/PK, человек, здоровые добровольцы)

**Шаг 3 — Введите CVintra**: введите значение вручную (кнопки 20/30/40/50% или число) и **обязательно поставьте галочку "I confirm"** — без неё N_det не рассчитается

**Шаг 4 — Настройте параметры** (секция 5): power, alpha, dropout, screen-fail

**Шаг 5 — Регуляторный ввод** (секция 7): washout, длительности, объём крови

**Шаг 6 — Нажмите "Run pipeline"** — система сделает всё сразу: поиск + PK + дизайн + N + регуляторные проверки

**Шаг 7 — Секции 3–6**: просмотр результатов (дизайн, N_det, DQI, Open Questions)

**Шаг 8 — Секция 8**: скачать .docx / .json / .md

> Секции 3–6 после Run pipeline заполняются автоматически. Кнопки "Подобрать дизайн" и "Compute N_det" — для ручного пошагового режима.
        """
    )

st.subheader("0) Метаданные протокола")
protocol_id = st.text_input("Protocol ID (optional)", value="", key="protocol_id")
protocol_status = "Draft" if not protocol_id.strip() else "Final"

col_meta1, col_meta2 = st.columns(2)
with col_meta1:
    dosage_form = st.text_input(
        "Лекарственная форма",
        value="",
        key="dosage_form",
        help="Например: таблетки, капсулы, раствор для инъекций",
    )
with col_meta2:
    dose = st.text_input(
        "Дозировка",
        value="",
        key="dose",
        help="Например: 500 mg, 10 mg/mL",
    )

replacement_subjects_label = st.selectbox("Replacement subjects / alternates", ["No", "Yes"], index=0)
replacement_subjects = replacement_subjects_label == "Yes"
visit_day_numbering = st.text_input("Visit/day numbering", value="continuous across periods")

col_cond1, col_cond2 = st.columns(2)
with col_cond1:
    protocol_condition_label = st.selectbox(
        "Режим приёма (fed/fasted/both)",
        ["", "fasted", "fed", "both"],
        index=0,
    )
    protocol_condition = protocol_condition_label or None
with col_cond2:
    study_phase_label = st.selectbox(
        "Тип исследования",
        ["auto", "single", "two-phase"],
        index=0,
        help="Однофазное / двухфазное / автовыбор моделью",
    )
    study_phase = study_phase_label if study_phase_label != "auto" else None

with st.expander("Предпочтительный дизайн и RSABE", expanded=False):
    preferred_design = st.text_input(
        "Предпочтительный дизайн (оставьте пустым для автовыбора)",
        value="",
        key="preferred_design",
        help="Например: 2x2_crossover, replicate, 4-way_replicate, parallel",
    )
    rsabe_requested = st.checkbox(
        "Необходимость применения RSABE",
        value=False,
        key="rsabe_requested",
        help="Если отмечено, система принудительно выберет replicate дизайн для RSABE",
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
            value="",
            key="age_range",
            help="Например: 18-55, 18-65",
        )
    additional_constraints = st.text_area(
        "Иные ограничения заказчика",
        value="",
        key="additional_constraints",
        help="Любые дополнительные требования к дизайну исследования",
    )


st.subheader("1) INN и источники")
inn = st.text_input("INN", value="metformin", key="inn", on_change=_reset_cv_on_inn_change)

with st.expander("Поиск источников (PubMed/PMC)", expanded=False):
    if st.button("Найти источники"):
        try:
            resp = api_post("/search_sources", {"inn": inn, "retmax": 10})
            st.session_state["sources"] = resp.get("sources", [])
            st.session_state["search"] = resp
            st.session_state["selected_sources"] = [s.get("pmid") for s in st.session_state["sources"]]
            st.success("Источники получены")
        except Exception as exc:
            st.error(f"Ошибка поиска: {exc}")

    sources = st.session_state.get("sources", [])
    if sources:
        df_sources = pd.DataFrame(
            [
                {
                    "pmid": s.get("pmid"),
                    "title": s.get("title"),
                    "year": s.get("year"),
                    "url": s.get("url"),
                }
                for s in sources
            ]
        )
        st.dataframe(df_sources, use_container_width=True)
        pmids = [s["pmid"] for s in sources]
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

st.markdown("## CVintra Confirmation (Required for N_det)")
st.warning("N_det is disabled until CVintra is confirmed.")
st.markdown(f"**CV source:** `{cv_source}`")

cv_confirmed_checked = st.checkbox(
    "I confirm CVintra value is correct and can be used for N_det",
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
    st.info("CVintra not available yet. You can enter a manual value below.")

if cv_source == "derived_from_ci":
    ci_low, ci_high, ci_n = _find_ci_for_cv(ci_values)
    st.info(
        "Assumptions for derived CV: 90% CI, 2x2 crossover, log-scale, correctness of n/CI. "
        f"CI_low={ci_low or '—'}, CI_high={ci_high or '—'}, n={ci_n or '—'}"
    )

_render_evidence(cv_evidence)

show_manual = cv_extracted_value is None or cv_source in ("range", "unknown") or dq_level == "red"
manual_cv_value = None
if show_manual:
    st.caption("Manual CV still requires confirmation.")
    use_manual_cv = st.checkbox("Use manual CV input", value=True, key="use_manual_cv")
    if use_manual_cv:
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
            "Manual CVintra (%)",
            value=float(manual_default),
            min_value=1.0,
            max_value=200.0,
            key="manual_cv_input",
        )
        if manual_cv_value and manual_cv_value > 0:
            st.session_state["manual_cv"] = float(manual_cv_value)

st.markdown("---")
st.subheader("▶ Run Pipeline (FullReport)")
st.info(
    "**Порядок действий перед запуском:**\n"
    "1. Заполните секцию 0 (метаданные, форма, доза, режим, пол, возраст)\n"
    "2. Введите INN в секции 1 и при необходимости выберите источники\n"
    "3. Введите CVintra выше и **поставьте галочку подтверждения**\n"
    "4. Выставьте power/alpha/dropout в секции 5\n"
    "5. Укажите washout в секции 7\n\n"
    "Затем нажмите кнопку ниже — система запустит весь pipeline одним запросом."
)

if st.button("▶ Run pipeline", type="primary"):
    seed_val = st.session_state.get("risk_seed")
    if seed_val == 0:
        seed_val = None
    risk_dist = st.session_state.get("risk_distribution") or None
    payload = {
        "inn": inn,
        "dosage_form": dosage_form.strip() or None,
        "dose": dose.strip() or None,
        "retmax": 10,
        "selected_sources": st.session_state.get("selected_sources") or None,
        "manual_cv": st.session_state.get("manual_cv"),
        "cv_confirmed": st.session_state.get("cv_confirmed", False),
        "rsabe_requested": rsabe_requested or None,
        "preferred_design": preferred_design.strip() or None,
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
        "protocol_condition": protocol_condition,
        "nti": st.session_state.get("nti_flag"),
        "study_phase": study_phase,
        "schedule_days": st.session_state.get("schedule_days") or None,
        "hospitalization_duration_days": st.session_state.get("hospitalization_duration_days") or None,
        "sampling_duration_days": st.session_state.get("sampling_duration_days") or None,
        "follow_up_duration_days": st.session_state.get("follow_up_duration_days") or None,
        "phone_follow_up_ok": st.session_state.get("phone_follow_up_ok"),
        "blood_volume_total_ml": st.session_state.get("blood_volume_total_ml") or None,
        "blood_volume_pk_ml": st.session_state.get("blood_volume_pk_ml") or None,
        "gender_requirement": gender_requirement or None,
        "age_range": age_range.strip() or None,
        "additional_constraints": additional_constraints.strip() or None,
    }
    try:
        resp = api_post("/run_pipeline", payload)
        st.session_state["fullreport"] = resp
        st.success("Pipeline complete")
    except Exception as exc:
        st.error(f"Ошибка pipeline: {exc}")


st.subheader("2) PK Extraction (optional)")
selected_sources = st.session_state.get("selected_sources", [])
if st.button("Извлечь PK"):
    try:
        resp = api_post("/extract_pk", {"inn": inn, "sources": selected_sources})
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
    if pk and pk.get("warnings"):
        st.warning("; ".join(pk.get("warnings")))
    if pk and pk.get("validation_issues"):
        st.warning(f"Validation issues: {pk.get('validation_issues')}")
    if study_condition:
        st.caption(f"Study condition: {study_condition}")
    if meal_details:
        details_text = ", ".join(
            [f"{key}={value}" for key, value in meal_details.items() if value not in (None, "")]
        )
        if details_text:
            st.caption(f"Meal details: {details_text}")


st.subheader("3) Design")
nti_flag = st.checkbox("NTI препарат", value=False, key="nti_flag")
design_resp = st.session_state.get("design")
design_from_report = _format_design(st.session_state.get("fullreport"), design_resp)
pk_payload = pk
if not pk_payload and st.session_state.get("fullreport"):
    fullreport_pk = (st.session_state.get("fullreport") or {}).get("pk_values")
    if fullreport_pk is not None:
        pk_payload = {
            "inn": inn,
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
        design_value = resp.get("recommendation") or resp.get("design") or "2x2 crossover"
        st.session_state["design"] = design_value
        st.success("Дизайн выбран")
        design_from_report = _format_design(st.session_state.get("fullreport"), resp)
    except Exception as exc:
        st.error(f"Ошибка дизайна: {exc}")
elif design_clicked and not pk_payload:
    st.warning("Нет PK данных для выбора дизайна. Запустите pipeline или извлеките PK.")

if design_from_report:
    st.write(design_from_report)


st.subheader("4) Оценка вариабельности (optional)")
colA, colB, colC = st.columns(3)
with colA:
    bcs_class = st.selectbox("BCS класс", [None, 1, 2, 3, 4], index=0)
with colB:
    logp = st.number_input("logP", value=0.0, min_value=-10.0, max_value=10.0,
                       help="Коэффициент липофильности. Может быть отрицательным.")
with colC:
    first_pass = st.selectbox("First-pass", [None, "low", "medium", "high"], index=0)

colD, colE = st.columns(2)
with colD:
    cyp = st.selectbox("CYP involvement", [None, "low", "medium", "high"], index=0)
with colE:
    nti_var = st.checkbox("NTI", value=False, key="nti_var")

if st.button("Оценить CV диапазон"):
    try:
        resp = api_post(
            "/variability_estimate",
            {
                "inn": inn,
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


st.subheader("5) Sample Size")
st.slider("Power", 0.5, 0.99, 0.8, key="power")
st.slider("Alpha", 0.01, 0.1, 0.05, key="alpha")
st.slider("Dropout", 0.0, 0.5, 0.1, key="dropout")
st.slider("Screen-fail", 0.0, 0.8, 0.1, key="screen_fail")

det_tab, risk_tab = st.tabs(["Deterministic (N_det)", "Risk-based (N_risk)"])

with det_tab:
    if not cv_confirmed:
        st.info("Disabled until CV confirmed. Go to CVintra Confirmation step.")

    sample_det = (st.session_state.get("fullreport") or {}).get("sample_size_det")
    if sample_det:
        st.write(sample_det)
    else:
        st.caption("N_det not computed (requires confirmed CV).")

    if st.button("Compute N_det", disabled=not cv_confirmed):
        design_value = design_from_report.get("design") if design_from_report else None
        cv_for_calc = manual_cv_value if manual_cv_value is not None else cv_extracted_value
        if not design_value:
            st.warning("Design not determined.")
        elif cv_for_calc is None:
            st.warning("CVintra value not provided.")
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
                        "dropout": float(st.session_state.get("dropout", 0.1)),
                        "screen_fail": float(st.session_state.get("screen_fail", 0.1)),
                    },
                )
                st.session_state["sample"] = resp
                st.success("N_det calculated")
                st.write(resp)
            except Exception as exc:
                st.error(f"Ошибка расчета N_det: {exc}")

with risk_tab:
    st.number_input("Risk seed (optional)", value=0, min_value=0, key="risk_seed")
    st.number_input("Monte Carlo sims", value=5000, min_value=1000, max_value=50000, key="risk_n_sims")
    st.text_input("CV distribution (optional)", value="", key="risk_distribution")

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
        st.caption("N_risk not computed (requires CV range/distribution).")


st.subheader("6) Data Quality + Reg-check")
data_quality = (st.session_state.get("fullreport") or {}).get("data_quality")
if data_quality:
    st.metric("Data Quality Index", value=str(data_quality.get("score", "—")))
    components = data_quality.get("components") or {}
    traceability = components.get("traceability")
    if traceability is not None:
        try:
            st.caption(f"Traceability component: {float(traceability):.2f}")
        except Exception:
            st.caption(f"Traceability component: {traceability}")
    st.write(data_quality)
else:
    st.info("Data Quality: Not computed.")

reg_checks = (st.session_state.get("fullreport") or {}).get("reg_check") or (st.session_state.get("reg") or {}).get("checks")
open_questions = (st.session_state.get("fullreport") or {}).get("open_questions") or (st.session_state.get("reg") or {}).get(
    "open_questions"
)

if reg_checks:
    st.write(reg_checks)
else:
    st.caption("Reg-check: No items.")

if open_questions:
    st.subheader("Open Questions / To clarify")
    for item in open_questions:
        st.write(f"- {item.get('question')} (priority: {item.get('priority')})")
else:
    st.caption("Open Questions: No items.")


st.subheader("7) Regulatory input (optional)")
st.number_input("Washout (days)", value=0.0, min_value=0.0, key="schedule_days")
with st.expander("Дополнительные параметры политики (опционально)"):
    st.number_input("Hospitalization duration (days)", value=0.0, min_value=0.0, key="hospitalization_duration_days")
    st.number_input("Sampling duration (days)", value=0.0, min_value=0.0, key="sampling_duration_days")
    st.number_input("Follow-up duration (days)", value=0.0, min_value=0.0, key="follow_up_duration_days")
    phone_follow_up_label = st.selectbox(
        "Phone follow-up acceptable?",
        ["unspecified", "Yes", "No"],
        index=0,
        key="phone_follow_up_label",
    )
    phone_follow_up_ok = None
    if phone_follow_up_label == "Yes":
        phone_follow_up_ok = True
    elif phone_follow_up_label == "No":
        phone_follow_up_ok = False
    st.session_state["phone_follow_up_ok"] = phone_follow_up_ok
    st.number_input("Blood volume total (mL)", value=0.0, min_value=0.0, key="blood_volume_total_ml")
    st.number_input("Blood volume PK-only (mL)", value=0.0, min_value=0.0, key="blood_volume_pk_ml")

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
    lines = [
        f"# Синопсис протокола исследования биоэквивалентности",
        "",
        f"**Действующее вещество (INN):** {report.get('inn', '—')}",
        f"**Лекарственная форма:** {report.get('dosage_form') or '—'}",
        f"**Дозировка:** {report.get('dose') or '—'}",
        f"**Номер протокола:** {report.get('protocol_id') or '—'}",
        f"**Статус:** {report.get('protocol_status') or '—'}",
        "",
        "## Цель исследования",
        f"Оценка биоэквивалентности тестового и референтного препаратов "
        f"действующего вещества {report.get('inn', '—')} у здоровых добровольцев.",
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
    lines.append(f"- **Режим приёма:** {report.get('protocol_condition') or '—'}")
    lines.append(f"- **Тип исследования:** {report.get('study_phase') or 'auto'}")
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
        lines.append("N_det не рассчитан (требуется подтверждённый CV).")
    lines.append("")
    lines.append("## Статистические методы")
    lines.append("ANOVA логарифмически преобразованных PK-параметров. 90% ДИ для Test/Reference. Критерий: 80.00–125.00%.")
    lines.append("")
    lines.append("## План мониторинга безопасности")
    lines.append("Мониторинг нежелательных явлений, витальных показателей и лабораторных данных на протяжении всего исследования.")
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
            pmid = s.get("pmid", "—")
            title = s.get("title", "—")
            year = s.get("year", "—")
            lines.append(f"{i}. {title} ({year}) PMID:{pmid}")
    else:
        lines.append("Источники не определены.")
    lines.append("")
    return "\n".join(lines)


st.subheader("8) Export")
fullreport_export = st.session_state.get("fullreport") or {
    "inn": inn,
    "dosage_form": dosage_form.strip() or None,
    "dose": dose.strip() or None,
    "protocol_id": protocol_id if protocol_id.strip() else None,
    "protocol_status": protocol_status,
    "replacement_subjects": replacement_subjects,
    "visit_day_numbering": visit_day_numbering,
    "protocol_condition": protocol_condition,
    "study_phase": study_phase,
    "gender_requirement": gender_requirement or None,
    "age_range": age_range.strip() or None,
    "additional_constraints": additional_constraints.strip() or None,
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
}

json_blob = json.dumps(fullreport_export, ensure_ascii=False, indent=2)

export_col1, export_col2, export_col3 = st.columns(3)
with export_col1:
    st.download_button(
        "Download FullReport.json",
        data=json_blob,
        file_name="FullReport.json",
        mime="application/json",
    )
with export_col2:
    md_text = _build_markdown_synopsis(fullreport_export)
    st.download_button(
        "Download synopsis.md",
        data=md_text,
        file_name="synopsis.md",
        mime="text/markdown",
    )

with export_col3:
    pass

if st.button("Build synopsis .docx"):
    try:
        resp = api_post("/build_docx", {"all_json": fullreport_export})
        if resp.get("warnings"):
            st.error("Docx render failed. See warnings.")
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
        "Download synopsis.docx",
        data=st.session_state["docx_bytes"],
        file_name=st.session_state.get("docx_filename") or "synopsis.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

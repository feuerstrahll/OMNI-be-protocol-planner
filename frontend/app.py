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


def _format_design(fullreport: Optional[Dict], design_resp: Optional[Dict]) -> Dict:
    if fullreport and fullreport.get("design"):
        return fullreport["design"]
    return design_resp or {}


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


st.subheader("0) Метаданные протокола")
protocol_id = st.text_input("Protocol ID (optional)", value="", key="protocol_id")
protocol_status = "Draft" if not protocol_id.strip() else "Final"
replacement_subjects_label = st.selectbox("Replacement subjects / alternates", ["No", "Yes"], index=0)
replacement_subjects = replacement_subjects_label == "Yes"
visit_day_numbering = st.text_input("Visit/day numbering", value="continuous across periods")


st.subheader("1) INN и источники")
inn = st.text_input("INN", value="metformin", key="inn")

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
        st.multiselect(
            "Выберите источники",
            options=[s["pmid"] for s in sources],
            default=st.session_state.get("selected_sources", []),
            key="selected_sources",
        )


fullreport = st.session_state.get("fullreport")
pk_state = st.session_state.get("pk")

cv_source, cv_value, cv_evidence, cv_info = _resolve_cv_context(fullreport, pk_state)
ci_values = _as_list((fullreport or {}).get("ci_values") or (pk_state or {}).get("ci_values"))
dq_level = _get((fullreport or {}).get("data_quality"), "level")

st.markdown("## CVintra Confirmation (Required for N_det)")
st.warning("N_det is disabled until CVintra is confirmed.")
st.markdown(f"**CV source:** `{cv_source}`")

cv_confirmed = st.checkbox(
    "I confirm CVintra value is correct and can be used for N_det",
    key="cv_confirmed",
    value=bool(st.session_state.get("cv_confirmed", False)),
)

if cv_value is not None:
    try:
        cv_display = f"{float(cv_value):.1f}%"
    except (TypeError, ValueError):
        cv_display = str(cv_value)
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

show_manual = cv_value is None or cv_source in ("range", "unknown") or dq_level == "red"
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
        manual_default = st.session_state.get("manual_cv", 30)
        manual_cv_value = st.number_input(
            "Manual CVintra (%)",
            value=float(manual_default),
            min_value=1.0,
            max_value=200.0,
            key="manual_cv",
        )

st.markdown("---")
st.subheader("Run Pipeline (FullReport)")

if st.button("Run pipeline", type="primary"):
    seed_val = st.session_state.get("risk_seed")
    if seed_val == 0:
        seed_val = None
    risk_dist = st.session_state.get("risk_distribution") or None
    payload = {
        "inn": inn,
        "retmax": 10,
        "selected_sources": st.session_state.get("selected_sources") or None,
        "manual_cv": manual_cv_value if show_manual else None,
        "cv_confirmed": bool(st.session_state.get("cv_confirmed", False)),
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
        "nti": st.session_state.get("nti_flag"),
        "schedule_days": st.session_state.get("schedule_days") or None,
        "hospitalization_duration_days": st.session_state.get("hospitalization_duration_days") or None,
        "sampling_duration_days": st.session_state.get("sampling_duration_days") or None,
        "follow_up_duration_days": st.session_state.get("follow_up_duration_days") or None,
        "phone_follow_up_ok": st.session_state.get("phone_follow_up_ok"),
        "blood_volume_total_ml": st.session_state.get("blood_volume_total_ml") or None,
        "blood_volume_pk_ml": st.session_state.get("blood_volume_pk_ml") or None,
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


st.subheader("3) Design")
nti_flag = st.checkbox("NTI препарат", value=False, key="nti_flag")
design_resp = st.session_state.get("design")
design_from_report = _format_design(st.session_state.get("fullreport"), design_resp)

if st.button("Подобрать дизайн") and pk:
    cv_payload = None
    cv_payload_value = manual_cv_value if manual_cv_value is not None else cv_value
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
        resp = api_post("/select_design", {"pk_json": pk, "cv_input": cv_payload, "nti": nti_flag})
        st.session_state["design"] = resp
        st.success("Дизайн выбран")
        design_from_report = _format_design(st.session_state.get("fullreport"), resp)
    except Exception as exc:
        st.error(f"Ошибка дизайна: {exc}")

if design_from_report:
    st.write(design_from_report)


st.subheader("4) Оценка вариабельности (optional)")
colA, colB, colC = st.columns(3)
with colA:
    bcs_class = st.selectbox("BCS класс", [None, 1, 2, 3, 4], index=0)
with colB:
    logp = st.number_input("logP", value=0.0, min_value=0.0, max_value=10.0)
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
        cv_for_calc = manual_cv_value if manual_cv_value is not None else cv_value
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

if st.button("Проверить чек-лист") and pk and design_from_report:
    cv_payload = None
    cv_payload_value = manual_cv_value if manual_cv_value is not None else cv_value
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
        resp = api_post(
            "/reg_check",
            {
                "design": design_from_report.get("design"),
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


st.subheader("8) Export")
fullreport_export = st.session_state.get("fullreport") or {
    "inn": inn,
    "protocol_id": protocol_id if protocol_id.strip() else None,
    "protocol_status": protocol_status,
    "replacement_subjects": replacement_subjects,
    "visit_day_numbering": visit_day_numbering,
    "sources": st.session_state.get("sources", []),
    "pk_values": (st.session_state.get("pk") or {}).get("pk_values", []),
    "ci_values": (st.session_state.get("pk") or {}).get("ci_values", []),
    "design": st.session_state.get("design"),
    "sample_size_det": st.session_state.get("sample"),
    "sample_size_risk": (st.session_state.get("fullreport") or {}).get("sample_size_risk"),
    "reg_check": (st.session_state.get("reg") or {}).get("checks", []),
    "open_questions": (st.session_state.get("reg") or {}).get("open_questions", []),
}

json_blob = json.dumps(fullreport_export, ensure_ascii=False, indent=2)
st.download_button(
    "Download FullReport.json",
    data=json_blob,
    file_name="FullReport.json",
    mime="application/json",
)

if st.button("Build synopsis .docx"):
    try:
        resp = api_post("/build_docx", {"all_json": fullreport_export})
        if resp.get("warnings"):
            st.error("Docx render failed. See warnings.")
            st.write(resp.get("warnings"))
        else:
            st.success(f"Docx создан: {resp.get('path_to_docx')}")
    except Exception as exc:
        st.error(f"Ошибка docx: {exc}")

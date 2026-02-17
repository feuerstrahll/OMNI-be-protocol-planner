import json
import os
from typing import Dict, List

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


st.subheader("1) Поиск источников (PubMed/PMC)")
inn = st.text_input("INN", value="metformin")

col1, col2 = st.columns(2)
with col1:
    if st.button("Найти источники"):
        try:
            resp = api_post("/search_sources", {"inn": inn, "retmax": 10})
            st.session_state["sources"] = resp.get("sources", [])
            st.session_state["search"] = resp
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
                "year": s.get("year", {}).get("value") if s.get("year") else None,
                "url": s.get("url"),
            }
            for s in sources
        ]
    )
    st.dataframe(df_sources, use_container_width=True)
    selected = st.multiselect("Выберите источники", options=[s["pmid"] for s in sources], default=[s["pmid"] for s in sources])
else:
    selected = []

st.subheader("2) Извлечение PK")
if st.button("Извлечь PK"):
    try:
        resp = api_post("/extract_pk", {"inn": inn, "sources": selected})
        st.session_state["pk"] = resp
        st.success("PK данные извлечены")
    except Exception as exc:
        st.error(f"Ошибка извлечения: {exc}")

pk = st.session_state.get("pk")
cv_value = None
if pk:
    pk_rows = []
    for pkv in pk.get("pk_values", []):
        ev = pkv["value"]["evidence"][0] if pkv["value"]["evidence"] else {}
        pk_rows.append(
            {
                "metric": pkv["metric"],
                "value": pkv["value"]["value"],
                "unit": pkv["value"].get("unit"),
                "source": ev.get("source"),
                "snippet": ev.get("snippet"),
            }
        )
        if pkv["metric"] == "CVintra":
            cv_value = pkv["value"]["value"]
    st.dataframe(pd.DataFrame(pk_rows), use_container_width=True)
    if pk.get("warnings"):
        st.warning("; ".join(pk.get("warnings")))
    if pk.get("validation_issues"):
        st.warning(f"Validation issues: {pk.get('validation_issues')}")

st.subheader("3) Подтверждение CVintra")
cv_confirmed = st.checkbox("Подтверждено", value=False)

cv_input_val = None
cv_unit = "%"

if cv_value is not None:
    st.write(f"Найден CVintra из источников: {cv_value}%")
    cv_input_val = st.number_input("CVintra (%)", value=float(cv_value), min_value=1.0, max_value=200.0)
else:
    st.info("CVintra не найден. Введите вручную или используйте пресеты.")
    preset_cols = st.columns(4)
    presets = [20, 30, 40, 50]
    for i, p in enumerate(presets):
        if preset_cols[i].button(f"{p}%"):
            st.session_state["cv_manual"] = p
    cv_default = st.session_state.get("cv_manual", 30)
    cv_input_val = st.number_input("CVintra (%)", value=float(cv_default), min_value=1.0, max_value=200.0)

st.subheader("4) Выбор дизайна")
nti_flag = st.checkbox("NTI препарат", value=False)
if st.button("Подобрать дизайн") and pk:
    cv_payload = None
    if cv_input_val is not None:
        cv_payload = {
            "cv": {
                "value": float(cv_input_val),
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
    except Exception as exc:
        st.error(f"Ошибка дизайна: {exc}")

if st.session_state.get("design"):
    st.write(st.session_state["design"])

st.subheader("5) Оценка вариабельности")
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
    nti = st.checkbox("NTI", value=False, key="nti_var")

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
                "nti": nti,
                "pk_json": pk,
            },
        )
        st.session_state["variability"] = resp
        st.success("Диапазон CV рассчитан")
    except Exception as exc:
        st.error(f"Ошибка вариабельности: {exc}")

if st.session_state.get("variability"):
    st.write(st.session_state["variability"])

st.subheader("6) Расчет N")
power = st.slider("Power", 0.5, 0.99, 0.8)
alpha = st.slider("Alpha", 0.01, 0.1, 0.05)
dropout = st.slider("Dropout", 0.0, 0.5, 0.1)
screen_fail = st.slider("Screen-fail", 0.0, 0.8, 0.1)

if st.button("Рассчитать N") and st.session_state.get("design"):
    cv_payload = {
        "cv": {
            "value": float(cv_input_val),
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
            "/calc_sample_size",
            {
                "design": st.session_state["design"]["design"],
                "cv_input": cv_payload,
                "power": power,
                "alpha": alpha,
                "dropout": dropout,
                "screen_fail": screen_fail,
            },
        )
        st.session_state["sample"] = resp
        st.success("N рассчитан")
    except Exception as exc:
        st.error(f"Ошибка расчета N: {exc}")

if st.session_state.get("sample"):
    st.write(st.session_state["sample"])

if cv_value is None:
    st.caption("Чувствительность N (приближенно)")
    sens_rows = []
    for p in [20, 30, 40, 50]:
        sens_rows.append({"CV%": p, "N_total": approx_n_total(p, power, alpha)})
    st.table(pd.DataFrame(sens_rows))

st.subheader("7) Оценка риска")
if st.button("Оценить риск") and st.session_state.get("sample") and st.session_state.get("variability"):
    try:
        resp = api_post(
            "/risk_estimate",
            {
                "design": st.session_state["design"]["design"],
                "N_total": st.session_state["sample"]["N_total"],
                "cv_range": st.session_state["variability"]["cv_range"],
                "distribution": "triangular",
                "n_sim": 2000,
            },
        )
        st.session_state["risk"] = resp
        st.success("Риск рассчитан")
    except Exception as exc:
        st.error(f"Ошибка риска: {exc}")

if st.session_state.get("risk"):
    st.write(st.session_state["risk"])

st.subheader("8) Регуляторный чек-лист")
schedule_days = st.number_input("Washout (days)", value=0.0, min_value=0.0)
if st.button("Проверить чек-лист") and pk and st.session_state.get("design"):
    cv_payload = {
        "cv": {
            "value": float(cv_input_val),
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
                "design": st.session_state["design"]["design"],
                "pk_json": pk,
                "schedule_days": schedule_days if schedule_days > 0 else None,
                "cv_input": cv_payload,
            },
        )
        st.session_state["reg"] = resp
        st.success("Чек-лист готов")
    except Exception as exc:
        st.error(f"Ошибка чек-листа: {exc}")

if st.session_state.get("reg"):
    st.write(st.session_state["reg"])

st.subheader("9) Сборка синопсиса")
if st.button("Собрать синопсис .docx"):
    payload = {
        "inn": inn,
        "search": st.session_state.get("search"),
        "pk": st.session_state.get("pk"),
        "design": st.session_state.get("design"),
        "sample": st.session_state.get("sample"),
        "variability": st.session_state.get("variability"),
        "risk": st.session_state.get("risk"),
        "reg": st.session_state.get("reg"),
    }
    try:
        resp = api_post("/build_docx", {"all_json": payload})
        st.success(f"Docx создан: {resp.get('path_to_docx')}")
    except Exception as exc:
        st.error(f"Ошибка docx: {exc}")

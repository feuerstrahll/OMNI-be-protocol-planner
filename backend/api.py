from __future__ import annotations

from typing import Optional

import os

from fastapi import APIRouter, FastAPI, HTTPException
from dotenv import load_dotenv

from backend.schemas import (
    BuildDocxRequest,
    BuildDocxResponse,
    DesignRequest,
    DesignResponse,
    FullReport,
    PKExtractionRequest,
    PKExtractionResponse,
    RegCheckRequest,
    RegCheckResponse,
    RiskRequest,
    RiskResponse,
    RunPipelineRequest,
    SampleSizeRequest,
    SampleSizeResponse,
    SearchSourcesRequest,
    SearchSourcesResponse,
    VariabilityInput,
    VariabilityResponse,
    CVInput,
    CVInfo,
    DesignDecision,
    NumericValue,
    OpenQuestion,
    SampleSizeDet,
    ValidationIssue,
)
from backend.services.cv_gate import select_cv_info
from backend.services.data_quality import compute_data_quality
from backend.services.design_engine import DesignEngine
from backend.services.docx_builder import DocxRenderError, build_docx
from backend.services.llm_pk_extractor import LLMDisabled, LLMPKExtractor
from backend.services.pk_extractor import PKExtractor
from backend.services.pmc_fetcher import fetch_pmc_sections
from backend.services.powertost_runner import health as powertost_health
from backend.services.pubmed_client import PubMedClient
from backend.services.reg_checker import RegChecker
from backend.services.risk_model import estimate_risk
from backend.services.sample_size import calc_sample_size
from backend.services.sample_size_risk import compute_sample_size_risk
from backend.services.utils import configure_logging, load_config
from backend.services.validator import PKValidator
from backend.services.variability_model import VariabilityModel
from backend.services.yandex_llm import YandexLLMClient

load_dotenv()
router = APIRouter()
logger = configure_logging()
config = load_config()

pubmed_client = PubMedClient(config)
_llm = None
if os.getenv("YANDEX_API_KEY") and os.getenv("YANDEX_FOLDER_ID"):
    try:
        _llm = YandexLLMClient()
    except Exception as exc:
        logger.warning("yandex_llm_init_failed", error=str(exc))
_llm_pk = None
try:
    _llm_pk = LLMPKExtractor()
except LLMDisabled:
    _llm_pk = None
except Exception as exc:
    logger.warning("llm_pk_init_failed", error=str(exc))
pk_extractor = PKExtractor(
    llm_client=_llm,
    pmc_fetcher=fetch_pmc_sections if _llm else None,
    llm_extractor=_llm_pk,
)
validator = PKValidator("backend/rules/validation_rules.yaml")
design_engine = DesignEngine("backend/rules/design_rules.yaml")
variability_model = VariabilityModel("backend/rules/variability_rules.yaml")
reg_checker = RegChecker("backend/rules/reg_rules.yaml")


@router.post("/search_sources", response_model=SearchSourcesResponse)
def search_sources(req: SearchSourcesRequest) -> SearchSourcesResponse:
    try:
        query, sources, warnings = pubmed_client.search_sources(req.inn, req.retmax)
    except Exception as exc:
        logger.error("search_sources_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="NCBI E-utilities request failed")

    return SearchSourcesResponse(inn=req.inn, query=query, sources=sources, warnings=warnings)


@router.post("/extract_pk", response_model=PKExtractionResponse)
def extract_pk(req: PKExtractionRequest) -> PKExtractionResponse:
    warnings = []
    try:
        abstracts = pubmed_client.fetch_abstracts(req.sources)
    except Exception as exc:
        logger.error("efetch_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="NCBI EFetch failed")

    if not abstracts:
        warnings.append("No abstracts returned from NCBI EFetch.")

    pk_values, ci_values, missing = pk_extractor.extract(abstracts, inn=req.inn)
    context = getattr(pk_extractor, "last_context", {}) or {}
    extractor_warnings = getattr(pk_extractor, "last_warnings", []) or []
    warnings.extend(extractor_warnings)
    validation_issues, validation_warnings = validator.validate_with_warnings(pk_values)
    warnings.extend(validation_warnings)
    if "clarify_meal_composition" in extractor_warnings:
        validation_issues.append(
            ValidationIssue(
                metric="study_condition",
                severity="WARN",
                message="Fed study detected but meal composition details are missing.",
            )
        )

    if not pk_values:
        warnings.append("No PK values extracted from abstracts. Consider manual input.")

    return PKExtractionResponse(
        inn=req.inn,
        pk_values=pk_values,
        ci_values=ci_values,
        study_condition=context.get("study_condition", "unknown"),
        meal_details=context.get("meal_details"),
        design_hints=context.get("design_hints"),
        warnings=warnings,
        missing=missing,
        validation_issues=validation_issues,
    )


@router.post("/select_design", response_model=DesignResponse)
def select_design(req: DesignRequest) -> DesignResponse:
    pk_values_calc, ci_values_calc, _ = _filter_pk_ci_for_calculation(
        req.pk_json.pk_values,
        req.pk_json.ci_values,
        None,
    )
    pk_json_calc = req.pk_json.model_copy(
        update={
            "pk_values": pk_values_calc,
            "ci_values": ci_values_calc,
        }
    )
    return design_engine.select_design(pk_json_calc, req.cv_input, req.nti)


@router.post("/calc_sample_size", response_model=SampleSizeResponse)
def calc_sample_size_endpoint(req: SampleSizeRequest) -> SampleSizeResponse:
    return calc_sample_size(req.design, req.cv_input, req.power, req.alpha, req.dropout, req.screen_fail)


@router.post("/variability_estimate", response_model=VariabilityResponse)
def variability_estimate(req: VariabilityInput) -> VariabilityResponse:
    if req.t_half is None and req.pk_json:
        for pk in req.pk_json.pk_values:
            if pk.name == "t1/2":
                req.t_half = pk.value
                break
    return variability_model.estimate(req)


@router.post("/risk_estimate", response_model=RiskResponse)
def risk_estimate(req: RiskRequest) -> RiskResponse:
    return estimate_risk(req)


@router.post("/reg_check", response_model=RegCheckResponse)
def reg_check(req: RegCheckRequest) -> RegCheckResponse:
    return reg_checker.run(
        req.design,
        req.pk_json,
        req.schedule_days,
        req.cv_input,
        nti=req.nti,
        hospitalization_duration_days=req.hospitalization_duration_days,
        sampling_duration_days=req.sampling_duration_days,
        follow_up_duration_days=req.follow_up_duration_days,
        phone_follow_up_ok=req.phone_follow_up_ok,
        blood_volume_total_ml=req.blood_volume_total_ml,
        blood_volume_pk_ml=req.blood_volume_pk_ml,
    )


@router.post("/build_docx", response_model=BuildDocxResponse)
def build_docx_endpoint(req: BuildDocxRequest) -> BuildDocxResponse:
    try:
        path = build_docx(req.all_json)
    except DocxRenderError as exc:
        logger.error("docx_render_failed", error=str(exc))
        return BuildDocxResponse(path_to_docx="", warnings=exc.warnings)
    except Exception as exc:
        logger.error("docx_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Docx build failed")

    return BuildDocxResponse(path_to_docx=path, warnings=[])


@router.get("/health/r")
def health_r() -> dict:
    return powertost_health()


@router.post("/run_pipeline", response_model=FullReport)
def run_pipeline(req: RunPipelineRequest) -> FullReport:
    # 1) Sources
    query = ""
    sources: list = []
    warnings: list[str] = []
    try:
        query, sources, warnings = pubmed_client.search_sources(req.inn, req.retmax)
    except Exception as exc:
        logger.error("search_sources_failed", error=str(exc))
        warnings.append("NCBI E-utilities request failed.")

    selected_sources = req.selected_sources or [s.pmid for s in sources]

    # 2) PK extraction
    pk_values: list = []
    ci_values: list = []
    missing: list[str] = []
    validation_issues: list = []
    context: dict = {}
    if selected_sources:
        try:
            abstracts = pubmed_client.fetch_abstracts(selected_sources)
            if abstracts:
                pk_values, ci_values, missing = pk_extractor.extract(abstracts, inn=req.inn)
                context = getattr(pk_extractor, "last_context", {}) or {}
                extractor_warnings = getattr(pk_extractor, "last_warnings", []) or []
                warnings.extend(extractor_warnings)
                validation_issues, validation_warnings = validator.validate_with_warnings(pk_values)
                warnings.extend(validation_warnings)
                if "clarify_meal_composition" in extractor_warnings:
                    validation_issues.append(
                        ValidationIssue(
                            metric="study_condition",
                            severity="WARN",
                            message="Fed study detected but meal composition details are missing.",
                        )
                    )
            else:
                warnings.append("No abstracts returned from NCBI EFetch.")
        except Exception as exc:
            logger.error("efetch_failed", error=str(exc))
            warnings.append("NCBI EFetch failed.")
    else:
        warnings.append("No sources selected for PK extraction.")

    pk_json = PKExtractionResponse(
        inn=req.inn,
        pk_values=pk_values,
        ci_values=ci_values,
        study_condition=(context or {}).get("study_condition", "unknown"),
        meal_details=(context or {}).get("meal_details"),
        design_hints=(context or {}).get("design_hints"),
        warnings=warnings,
        missing=missing,
        validation_issues=validation_issues,
    )

    pk_values_calc, ci_values_calc, calc_notes = _filter_pk_ci_for_calculation(
        pk_values,
        ci_values,
        req.protocol_condition,
    )
    if calc_notes:
        warnings.extend(calc_notes)

    pk_json_calc = PKExtractionResponse(
        inn=req.inn,
        pk_values=pk_values_calc,
        ci_values=ci_values_calc,
        study_condition=pk_json.study_condition,
        meal_details=pk_json.meal_details,
        design_hints=pk_json.design_hints,
        warnings=pk_json.warnings,
        missing=pk_json.missing,
        validation_issues=pk_json.validation_issues,
    )

    # 3) CV info (gate)
    cv_info, cv_questions = select_cv_info(
        pk_json_calc,
        ci_values_calc,
        req.manual_cv,
        req.cv_confirmed,
        variability_model,
        use_fallback=req.use_fallback,
    )

    # 4) Data quality
    data_quality = compute_data_quality(
        pk_values,
        ci_values,
        sources,
        cv_info,
        validation_issues,
        use_mock_extractor=req.use_mock_extractor,
    )

    # 5) Design
    design_resp = design_engine.select_design(pk_json_calc, _cv_input_from_cvinfo(cv_info), req.nti)
    design_decision = DesignDecision(
        recommendation=design_resp.design,
        reasoning_rule_id=design_resp.reasoning_rule_id
        or (design_resp.reasoning[0].rule_id if design_resp.reasoning else None),
        reasoning_text=design_resp.reasoning_text
        or (design_resp.reasoning[0].message if design_resp.reasoning else ""),
        required_inputs_missing=design_resp.required_inputs_missing or [],
    )

    # 6) Sample size (deterministic)
    sample_det = None
    if cv_info.value is not None and data_quality.allow_n_det:
        cv_input = _cv_input_from_cvinfo(cv_info)
        if cv_input:
            sample_resp = calc_sample_size(
                design_decision.recommendation,
                cv_input,
                req.power,
                req.alpha,
                req.dropout,
                req.screen_fail,
            )
            sample_det = SampleSizeDet(
                design=design_decision.recommendation,
                alpha=req.alpha,
                power=req.power,
                cv=cv_info.value,
                n_total=int(sample_resp.N_total.value) if sample_resp.N_total else None,
                n_rand=int(sample_resp.N_rand.value) if sample_resp.N_rand else None,
                n_screen=int(sample_resp.N_screen.value) if sample_resp.N_screen else None,
                dropout=req.dropout,
                screen_fail=req.screen_fail,
                powertost_details=sample_resp.details.get("raw") if sample_resp.details else None,
                warnings=sample_resp.warnings,
            )

    # 6b) Sample size (risk/Monte Carlo)
    sample_risk = None
    if cv_info.cv_source == "range" and cv_info.range_low is not None and cv_info.range_high is not None:
        sample_risk, risk_warnings = compute_sample_size_risk(
            req.inn,
            cv_info,
            req.alpha,
            req.power,
            req.risk_n_sims,
            req.risk_seed,
            req.risk_distribution,
        )
        if risk_warnings:
            warnings.extend(risk_warnings)

    # 7) Reg checks + Open questions
    reg_resp = reg_checker.run(
        design_decision.recommendation,
        pk_json,
        req.schedule_days,
        _cv_input_from_cvinfo(cv_info),
        nti=req.nti,
        hospitalization_duration_days=req.hospitalization_duration_days,
        sampling_duration_days=req.sampling_duration_days,
        follow_up_duration_days=req.follow_up_duration_days,
        phone_follow_up_ok=req.phone_follow_up_ok,
        blood_volume_total_ml=req.blood_volume_total_ml,
        blood_volume_pk_ml=req.blood_volume_pk_ml,
        data_quality=data_quality,
        cv_info=cv_info,
        validation_issues=validation_issues,
    )

    # 8) Protocol ID
    protocol_id, protocol_status = _resolve_protocol_id(req.protocol_id, req.inn)

    open_questions_list = list(reg_resp.open_questions or []) + cv_questions
    if req.protocol_condition and "condition_tagging_missing" in calc_notes:
        open_questions_list.append(
            OpenQuestion(
                category="feeding",
                question="Condition-specific tagging not available; manual confirmation required.",
                priority="medium",
                linked_rule_id="FEEDING_CONDITION_TAGGING",
            )
        )
    open_questions = _dedupe_open_questions(open_questions_list)

    return FullReport(
        inn=req.inn,
        protocol_id=protocol_id,
        protocol_status=protocol_status,
        replacement_subjects=req.replacement_subjects,
        visit_day_numbering=req.visit_day_numbering,
        protocol_condition=req.protocol_condition,
        sources=sources,
        pk_values=pk_values,
        ci_values=ci_values,
        study_condition=pk_json.study_condition,
        meal_details=pk_json.meal_details,
        design_hints=pk_json.design_hints,
        cv_info=cv_info,
        data_quality=data_quality,
        design=design_decision,
        sample_size_det=sample_det,
        sample_size_risk=sample_risk,
        reg_check=reg_resp.checks,
        open_questions=open_questions,
        audit_trail=[],
    )


def _resolve_protocol_id(protocol_id: Optional[str], inn: str) -> tuple[str, str]:
    if protocol_id and protocol_id.strip():
        return protocol_id.strip(), "Final"
    from backend.services.utils import now_iso

    date_str = now_iso().split("T")[0].replace("-", "")
    return f"BE-{inn}-{date_str}", "Draft"


def _cv_input_from_cvinfo(cv_info: CVInfo):
    if cv_info.value is None:
        return None
    return CVInput(
        cv=NumericValue(
            value=float(cv_info.value),
            unit="%",
            evidence=cv_info.evidence,
        ),
        confirmed=cv_info.confirmed_by_user,
    )


def _dedupe_open_questions(open_questions):
    seen = set()
    out = []
    for item in open_questions:
        question = (item.get("question") if isinstance(item, dict) else item.question) or ""
        key = " ".join(question.lower().split())
        if key in seen:
            continue
        out.append(item)
        seen.add(key)
    return out


app = FastAPI(title="OMNI BE Protocol Planner")
app.include_router(router)


def _filter_pk_ci_for_calculation(
    pk_values: list,
    ci_values: list,
    protocol_condition: Optional[str],
) -> tuple[list, list, list[str]]:
    warnings: list[str] = []
    filtered_pk: list = []
    filtered_ci: list = []
    tagging_missing = False

    for pk in pk_values:
        if not _is_ambiguous(pk):
            filtered_pk.append(pk)
            continue
        if protocol_condition and _matches_protocol_condition(pk, protocol_condition):
            filtered_pk.append(pk)
        else:
            if protocol_condition and not _has_condition_tags(pk):
                tagging_missing = True

    for ci in ci_values:
        if not _is_ambiguous(ci):
            filtered_ci.append(ci)
            continue
        if protocol_condition and _matches_protocol_condition(ci, protocol_condition):
            filtered_ci.append(ci)
        else:
            if protocol_condition and not _has_condition_tags(ci):
                tagging_missing = True

    if protocol_condition and tagging_missing:
        warnings.append("condition_tagging_missing")

    return filtered_pk, filtered_ci, warnings


def _is_ambiguous(item) -> bool:
    ambiguous = getattr(item, "ambiguous_condition", None)
    if ambiguous:
        return True
    warnings = getattr(item, "warnings", None)
    return bool(warnings and "ambiguous_condition" in warnings)


def _has_condition_tags(item) -> bool:
    evidence = getattr(item, "evidence", None) or []
    for ev in evidence:
        tags = getattr(ev, "context_tags", None) or {}
        if tags.get("fed") or tags.get("fasted"):
            return True
    return False


def _matches_protocol_condition(item, protocol_condition: str) -> bool:
    if protocol_condition not in ("fed", "fasted"):
        return False
    evidence = getattr(item, "evidence", None) or []
    for ev in evidence:
        tags = getattr(ev, "context_tags", None) or {}
        if tags.get(protocol_condition):
            return True
    return False

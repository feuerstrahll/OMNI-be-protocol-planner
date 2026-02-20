from __future__ import annotations

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
    ValidationIssue,
    VariabilityInput,
    VariabilityResponse,
)
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
from backend.services.pipeline import filter_pk_ci_for_calculation, run_pipeline as run_pipeline_service
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
    validation_issues, validation_warnings = validator.validate_with_warnings(pk_values, ci_values)
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
    pk_values_calc, ci_values_calc, _ = filter_pk_ci_for_calculation(
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
        protocol_condition=req.protocol_condition,
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
    return run_pipeline_service(
        req,
        pubmed_client=pubmed_client,
        pk_extractor=pk_extractor,
        validator=validator,
        design_engine=design_engine,
        variability_model=variability_model,
        reg_checker=reg_checker,
        logger=logger,
    )


app = FastAPI(title="OMNI BE Protocol Planner")
app.include_router(router)

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
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
    SampleSizeDet,
)
from backend.services.cv_gate import select_cv_info
from backend.services.data_quality import compute_data_quality
from backend.services.design_engine import DesignEngine
from backend.services.docx_builder import DocxRenderError, build_docx
from backend.services.pk_extractor import PKExtractor
from backend.services.powertost_runner import health as powertost_health
from backend.services.pubmed_client import PubMedClient
from backend.services.reg_checker import RegChecker
from backend.services.risk_model import estimate_risk
from backend.services.sample_size import calc_sample_size
from backend.services.sample_size_risk import compute_sample_size_risk
from backend.services.utils import configure_logging, load_config
from backend.services.validator import PKValidator
from backend.services.variability_model import VariabilityModel

load_dotenv()
router = APIRouter()
logger = configure_logging()
config = load_config()

pubmed_client = PubMedClient(config)
pk_extractor = PKExtractor()
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

    pk_values, ci_values, missing = pk_extractor.extract(abstracts)
    validation_issues, validation_warnings = validator.validate_with_warnings(pk_values)
    warnings.extend(validation_warnings)

    if not pk_values:
        warnings.append("No PK values extracted from abstracts. Consider manual input.")

    return PKExtractionResponse(
        inn=req.inn,
        pk_values=pk_values,
        ci_values=ci_values,
        warnings=warnings,
        missing=missing,
        validation_issues=validation_issues,
    )


@router.post("/select_design", response_model=DesignResponse)
def select_design(req: DesignRequest) -> DesignResponse:
    return design_engine.select_design(req.pk_json, req.cv_input, req.nti)


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
    if selected_sources:
        try:
            abstracts = pubmed_client.fetch_abstracts(selected_sources)
            if abstracts:
                pk_values, ci_values, missing = pk_extractor.extract(abstracts)
                validation_issues, validation_warnings = validator.validate_with_warnings(pk_values)
                warnings.extend(validation_warnings)
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
        warnings=warnings,
        missing=missing,
        validation_issues=validation_issues,
    )

    # 3) CV info (gate)
    cv_info, cv_questions = select_cv_info(
        pk_json,
        ci_values,
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
    design_resp = design_engine.select_design(pk_json, _cv_input_from_cvinfo(cv_info), req.nti)
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

    open_questions = _dedupe_open_questions(list(reg_resp.open_questions or []) + cv_questions)

    return FullReport(
        inn=req.inn,
        protocol_id=protocol_id,
        protocol_status=protocol_status,
        replacement_subjects=req.replacement_subjects,
        visit_day_numbering=req.visit_day_numbering,
        sources=sources,
        pk_values=pk_values,
        ci_values=ci_values,
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

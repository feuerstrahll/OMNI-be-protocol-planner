from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from dotenv import load_dotenv

from backend.schemas import (
    BuildDocxRequest,
    BuildDocxResponse,
    DesignRequest,
    DesignResponse,
    PKExtractionRequest,
    PKExtractionResponse,
    RegCheckRequest,
    RegCheckResponse,
    RiskRequest,
    RiskResponse,
    SampleSizeRequest,
    SampleSizeResponse,
    SearchSourcesRequest,
    SearchSourcesResponse,
    VariabilityInput,
    VariabilityResponse,
)
from backend.services.design_engine import DesignEngine
from backend.services.docx_builder import build_docx
from backend.services.pk_extractor import PKExtractor
from backend.services.pubmed_client import PubMedClient
from backend.services.reg_checker import RegChecker
from backend.services.risk_model import estimate_risk
from backend.services.sample_size import calc_sample_size
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

    pk_values, missing = pk_extractor.extract(abstracts)
    validation_issues = validator.validate(pk_values)

    if not pk_values:
        warnings.append("No PK values extracted from abstracts. Consider manual input.")

    return PKExtractionResponse(
        inn=req.inn,
        pk_values=pk_values,
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
            if pk.metric == "t1/2":
                req.t_half = pk.value
                break
    return variability_model.estimate(req)


@router.post("/risk_estimate", response_model=RiskResponse)
def risk_estimate(req: RiskRequest) -> RiskResponse:
    return estimate_risk(req)


@router.post("/reg_check", response_model=RegCheckResponse)
def reg_check(req: RegCheckRequest) -> RegCheckResponse:
    return reg_checker.run(req.design, req.pk_json, req.schedule_days, req.cv_input)


@router.post("/build_docx", response_model=BuildDocxResponse)
def build_docx_endpoint(req: BuildDocxRequest) -> BuildDocxResponse:
    try:
        path = build_docx(req.all_json)
    except Exception as exc:
        logger.error("docx_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Docx build failed")

    return BuildDocxResponse(path_to_docx=path, warnings=[])

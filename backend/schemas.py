from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Optional[str] = Field(None, description="Internal source identifier")
    pmid_or_url: Optional[str] = Field(None, description="PMID or URL")
    pmid: Optional[str] = Field(None, description="PMID if available")
    url: Optional[str] = Field(None, description="Source URL if available")
    excerpt: Optional[str] = Field(None, description="Evidence excerpt")
    location: Optional[str] = Field(None, description="abstract/table/section if available")
    confidence: Optional[str] = Field(None, description="Optional confidence label")
    context_tags: Optional[Dict[str, bool]] = Field(None, description="Context tags extracted from evidence snippet")

    # Legacy fields to keep compatibility with existing services.
    source_type: Optional[Literal["PMID", "URL"]] = None
    source: Optional[str] = None
    snippet: Optional[str] = None
    context: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy(cls, data: Any) -> Any:
        if isinstance(data, Evidence):
            return data
        if not isinstance(data, dict):
            return data
        payload = dict(data)

        if payload.get("excerpt") is None and payload.get("snippet"):
            payload["excerpt"] = payload["snippet"]
        if payload.get("location") is None and payload.get("context"):
            payload["location"] = payload["context"]

        source = payload.get("source")
        if not payload.get("pmid") and isinstance(source, str) and source.startswith("PMID:"):
            payload["pmid"] = source.replace("PMID:", "")
        if not payload.get("url") and isinstance(source, str) and source.startswith("http"):
            payload["url"] = source
        if not payload.get("pmid_or_url"):
            pmid = payload.get("pmid")
            url = payload.get("url")
            payload["pmid_or_url"] = pmid or url or source
        elif isinstance(payload.get("pmid_or_url"), str):
            pmid_or_url = payload.get("pmid_or_url")
            if not payload.get("url") and pmid_or_url.startswith("http"):
                payload["url"] = pmid_or_url
            if not payload.get("pmid") and (pmid_or_url.isdigit() or pmid_or_url.startswith("PMID:")):
                payload["pmid"] = pmid_or_url.replace("PMID:", "")
        if not payload.get("source_id"):
            payload["source_id"] = payload.get("pmid") or payload.get("url") or source
        return payload


class NumericValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    unit: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)

class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pmid: str
    title: str
    year: Optional[int] = None
    type_tags: List[Literal["BE", "PK", "review"]] = Field(default_factory=list)
    species: Optional[Literal["human", "animal"]] = None
    feeding: Optional[Literal["fasted", "fed"]] = None
    url: Optional[str] = None


class SearchSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    retmax: int = Field(10, ge=1, le=50)


class SearchSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    query: str
    sources: List[SourceCandidate]
    warnings: List[str] = Field(default_factory=list)


class PKValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    normalized_value: Optional[float] = None
    normalized_unit: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)
    warnings: List[str] = Field(
        default_factory=list,
        description="Extraction/validation warnings (e.g., llm_extracted_requires_human_review)",
    )
    conflict_sources: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy(cls, data: Any) -> Any:
        if isinstance(data, PKValue):
            return data
        if not isinstance(data, dict):
            return data
        payload = dict(data)

        metric = payload.get("metric")
        if metric and not payload.get("name"):
            payload["name"] = metric

        val = payload.get("value")
        if isinstance(val, dict):
            payload["value"] = val.get("value")
            payload.setdefault("unit", val.get("unit"))
            if not payload.get("evidence") and val.get("evidence"):
                payload["evidence"] = val.get("evidence")

        return payload


class PKExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    sources: List[str] = Field(..., description="List of PMIDs or PMCID:... identifiers")


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Optional[str]
    severity: Literal["ERROR", "WARN"]
    message: str


class PKExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    pk_values: List[PKValue]
    ci_values: List[CIValue] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    validation_issues: List[ValidationIssue] = Field(default_factory=list)


class CIValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    param: Literal["AUC", "Cmax"]
    ci_low: float
    ci_high: float
    confidence_level: float = 0.90
    n: Optional[int] = None
    design_hint: Optional[str] = None
    gmr: Optional[float] = None
    evidence: List[Evidence] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CVInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Optional[float] = None
    source: Literal["reported", "derived_from_ci", "manual", "range", "unknown"] = "unknown"
    cv_source: Optional[Literal["reported", "derived_from_ci", "manual", "range", "unknown"]] = None
    confidence: Optional[Literal["low", "medium", "high"]] = None
    requires_human_confirm: bool = True
    confirmed_by_user: bool = False
    evidence: List[Evidence] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    range_mode: Optional[float] = None
    range_drivers: List[str] = Field(default_factory=list)
    range_confidence: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _sync_source(cls, data: Any) -> Any:
        if isinstance(data, CVInfo):
            return data
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if payload.get("cv_source") is None and payload.get("source") is not None:
            payload["cv_source"] = payload.get("source")
        if payload.get("source") is None and payload.get("cv_source") is not None:
            payload["source"] = payload.get("cv_source")
        return payload


class DataQualityComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completeness: float = Field(0.0, ge=0.0, le=1.0)
    traceability: float = Field(0.0, ge=0.0, le=1.0)
    plausibility: float = Field(0.0, ge=0.0, le=1.0)
    consistency: float = Field(0.0, ge=0.0, le=1.0)
    source_quality: float = Field(0.0, ge=0.0, le=1.0)


class DataQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(0, ge=0, le=100)
    level: Literal["green", "yellow", "red"] = "red"
    components: DataQualityComponents
    reasons: List[str] = Field(default_factory=list)
    allow_n_det: bool = False
    prefer_n_risk: bool = True


class DesignDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str
    reasoning_rule_id: Optional[str] = None
    reasoning_text: str
    required_inputs_missing: List[str] = Field(default_factory=list)


class SampleSizeDet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: str
    alpha: float
    power: float
    cv: Optional[float] = None
    n_total: Optional[int] = None
    n_rand: Optional[int] = None
    n_screen: Optional[int] = None
    dropout: float
    screen_fail: float
    powertost_details: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class SampleSizeRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cv_distribution: str
    n_targets: Dict[str, int] = Field(default_factory=dict)
    p_success_at_n: Dict[str, float] = Field(default_factory=dict)
    sensitivity_notes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    seed: Optional[int] = None
    n_sims: Optional[int] = None
    rng_name: Optional[str] = None
    method: Optional[str] = None
    numpy_version: Optional[str] = None
    scipy_version: Optional[str] = None


class RegCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "RISK", "CLARIFY"]
    message: str
    what_to_clarify: List[str] = Field(default_factory=list)
    rule_id: Optional[str] = None


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    question: str
    priority: Literal["low", "medium", "high"] = "medium"
    linked_rule_id: Optional[str] = None


class AuditTrailItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    step: str
    rule_id: Optional[str] = None
    input_snapshot: Dict[str, Any] = Field(default_factory=dict)
    output_snapshot: Dict[str, Any] = Field(default_factory=dict)
    human_confirmations: List[str] = Field(default_factory=list)


class FullReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    protocol_id: Optional[str] = None
    protocol_status: Optional[str] = None
    replacement_subjects: Optional[bool] = None
    visit_day_numbering: Optional[str] = None
    sources: List[SourceCandidate] = Field(default_factory=list)
    pk_values: List[PKValue] = Field(default_factory=list)
    ci_values: List[CIValue] = Field(default_factory=list)
    cv_info: CVInfo
    data_quality: DataQuality
    design: Optional[DesignDecision] = None
    sample_size_det: Optional[SampleSizeDet] = None
    sample_size_risk: Optional[SampleSizeRisk] = None
    reg_check: List[RegCheckItem] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    audit_trail: List[AuditTrailItem] = Field(default_factory=list)


class CVInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cv: NumericValue
    confirmed: bool = Field(..., description="Must be confirmed in UI")


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pk_json: PKExtractionResponse
    cv_input: Optional[CVInput] = None
    nti: Optional[bool] = None


class DesignReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    message: str


class DesignResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: str
    reasoning: List[DesignReason]
    reasoning_rule_id: Optional[str] = None
    reasoning_text: str = ""
    required_inputs_missing: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SampleSizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: str
    cv_input: CVInput
    power: float = Field(0.8, ge=0.5, le=0.99)
    alpha: float = Field(0.05, ge=0.01, le=0.1)
    dropout: float = Field(0.0, ge=0.0, le=0.5)
    screen_fail: float = Field(0.0, ge=0.0, le=0.8)


class SampleSizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    N_total: Optional[NumericValue]
    N_rand: Optional[NumericValue]
    N_screen: Optional[NumericValue]
    details: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class VariabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    bcs_class: Optional[int] = Field(None, ge=1, le=4)
    logp: Optional[float] = None
    t_half: Optional[float] = None
    first_pass: Optional[Literal["low", "medium", "high"]] = None
    cyp_involvement: Optional[Literal["low", "medium", "high"]] = None
    nti: Optional[bool] = None
    pk_json: Optional[PKExtractionResponse] = None


class CVRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: NumericValue
    high: NumericValue
    mode: Optional[NumericValue] = None


class VariabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cv_range: CVRange
    drivers: List[str]
    confidence: Literal["low", "medium", "high"]
    warnings: List[str] = Field(default_factory=list)


class RiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: str
    N_total: NumericValue
    cv_range: CVRange
    alpha: float = Field(0.05, ge=0.01, le=0.1)
    theta1: float = Field(0.8, ge=0.7, le=0.95)
    theta2: float = Field(1.25, ge=1.05, le=1.43)
    distribution: Literal["triangular", "lognormal"] = "triangular"
    n_sim: int = Field(2000, ge=2000, le=20000)


class RiskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p_success: NumericValue
    risk_level: Literal["green", "yellow", "red"]
    drivers: List[str]
    assumptions: List[str]
    warnings: List[str] = Field(default_factory=list)


class RegCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design: str
    pk_json: PKExtractionResponse
    schedule_days: Optional[float] = None
    cv_input: Optional[CVInput] = None
    hospitalization_duration_days: Optional[float] = None
    sampling_duration_days: Optional[float] = None
    follow_up_duration_days: Optional[float] = None
    phone_follow_up_ok: Optional[bool] = None
    blood_volume_total_ml: Optional[float] = None
    blood_volume_pk_ml: Optional[float] = None


class RegCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: List[RegCheckItem]
    open_questions: List[OpenQuestion] = Field(default_factory=list)


class BuildDocxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_json: Dict[str, Any]


class BuildDocxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_to_docx: str
    warnings: List[str] = Field(default_factory=list)


class RunPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    retmax: int = Field(10, ge=1, le=50)
    selected_sources: Optional[List[str]] = None
    manual_cv: Optional[float] = None
    cv_confirmed: bool = False
    power: float = Field(0.8, ge=0.5, le=0.99)
    alpha: float = Field(0.05, ge=0.01, le=0.1)
    dropout: float = Field(0.0, ge=0.0, le=0.5)
    screen_fail: float = Field(0.0, ge=0.0, le=0.8)
    use_mock_extractor: bool = False
    use_fallback: bool = False
    risk_seed: Optional[int] = None
    risk_n_sims: int = Field(5000, ge=1000, le=50000)
    risk_distribution: Optional[str] = None
    protocol_id: Optional[str] = None
    replacement_subjects: bool = False
    visit_day_numbering: str = "continuous across periods"
    nti: Optional[bool] = None
    schedule_days: Optional[float] = None
    hospitalization_duration_days: Optional[float] = None
    sampling_duration_days: Optional[float] = None
    follow_up_duration_days: Optional[float] = None
    phone_follow_up_ok: Optional[bool] = None
    blood_volume_total_ml: Optional[float] = None
    blood_volume_pk_ml: Optional[float] = None

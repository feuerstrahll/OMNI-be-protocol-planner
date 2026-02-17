from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["PMID", "URL"] = Field(..., description="PMID or URL provenance")
    source: str = Field(..., description="PMID:<id> or URL")
    snippet: str = Field(..., description="Evidence snippet or context")
    context: Optional[str] = Field(None, description="Additional context")


class NumericValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    unit: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pmid: str
    title: str
    year: Optional[NumericValue] = None
    journal: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None


class SearchSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    retmax: int = Field(10, ge=1, le=50)


class SearchSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    query: str
    sources: List[SourceRecord]
    warnings: List[str] = Field(default_factory=list)


class PKValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal[
        "Cmax",
        "AUC",
        "AUC_inf",
        "AUC_last",
        "t1/2",
        "Tmax",
        "CVintra",
    ]
    value: NumericValue
    confidence: Literal["low", "medium", "high"] = "medium"


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
    warnings: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    validation_issues: List[ValidationIssue] = Field(default_factory=list)


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
    t_half: Optional[NumericValue] = None
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


class RegCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["OK", "RISK", "CLARIFY"]
    message: str
    what_to_clarify: Optional[str] = None


class RegCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: List[RegCheckItem]


class BuildDocxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_json: Dict[str, Any]


class BuildDocxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_to_docx: str
    warnings: List[str] = Field(default_factory=list)

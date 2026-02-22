from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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
    offset_start: Optional[int] = Field(None, description="Optional start offset of snippet in source text")
    offset_end: Optional[int] = Field(None, description="Optional end offset of snippet in source text")

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


SourceIdType = Literal["PMID", "PMCID", "URL"]


class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_type: SourceIdType = Field(..., description="Type of identifier: PMID, PMCID, or URL")
    id: str = Field(..., description="Clean identifier without prefix (e.g. 123, not PMID:123)")
    url: Optional[str] = Field(None, description="Source URL")
    title: str
    year: Optional[int] = None
    journal: Optional[str] = Field(None, description="Journal name when available")
    type_tags: List[Literal["BE", "PK", "review"]] = Field(default_factory=list)
    species: Optional[Literal["human", "animal"]] = None
    feeding: Optional[Literal["fasted", "fed"]] = None
    alt_ids: Optional[List[str]] = Field(None, description="Other ids for same article, e.g. [PMCID:123] when canonical is PMID")

    @computed_field
    @property
    def ref_id(self) -> str:
        """Canonical display id for API/UI: PMID:123 or PMCID:123 (no mixed junk)."""
        return f"{self.id_type}:{self.id}"

    @model_validator(mode="before")
    @classmethod
    def _normalize_id_from_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if payload.get("id_type") and payload.get("id") is not None:
            return payload
        ref = (payload.get("ref_id") or "").strip()
        if ref.upper().startswith("PMCID:"):
            payload["id_type"] = "PMCID"
            payload["id"] = ref.split(":", 1)[1].strip().lstrip("PMC")
        elif ref.upper().startswith("PMID:"):
            payload["id_type"] = "PMID"
            payload["id"] = ref.split(":", 1)[1].strip()
        elif ref.startswith("http"):
            payload["id_type"] = "URL"
            payload["id"] = ref
        elif ref:
            payload["id_type"] = "PMID"
            payload["id"] = ref
        else:
            leg_pmcid = payload.get("pmcid")
            leg_pmid = payload.get("pmid")
            if leg_pmcid is not None and str(leg_pmcid).strip():
                payload["id_type"] = "PMCID"
                payload["id"] = str(leg_pmcid).strip().lstrip("PMC")
            elif leg_pmid is not None and str(leg_pmid).strip():
                p = str(leg_pmid).strip()
                if p.upper().startswith("PMCID:"):
                    payload["id_type"] = "PMCID"
                    payload["id"] = p.split(":", 1)[1].strip().lstrip("PMC")
                else:
                    payload["id_type"] = "PMID"
                    payload["id"] = p.replace("PMID:", "").strip()
            else:
                payload.setdefault("id_type", "PMID")
                payload.setdefault("id", "")
        for key in ("ref_id", "pmid", "pmcid"):
            payload.pop(key, None)
        return payload


StudyCondition = Literal["fed", "fasted", "unknown"]


class MealDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calories_kcal: Optional[int] = None
    fat_g: Optional[int] = None
    timing_min: Optional[int] = None
    note: Optional[str] = None


class DesignHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_crossover_2x2: Optional[bool] = None
    log_transform: Optional[bool] = None
    n: Optional[int] = None


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
    ambiguous_condition: Optional[bool] = None

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


class CIValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    param: Literal["AUC", "Cmax"]
    ci_low: float
    ci_high: float
    ci_type: Literal["ratio", "percent"] = "ratio"
    confidence_level: float = 0.90
    n: Optional[int] = None
    design_hint: Optional[str] = None
    gmr: Optional[float] = None
    evidence: List[Evidence] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    ambiguous_condition: Optional[bool] = None


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Optional[str]
    severity: Literal["ERROR", "WARN"]
    message: str


class CVInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Optional[float] = None
    # Legacy; kept in sync with cv_source via _sync_source. TODO: Remove 'source' in v2.
    source: Literal["reported", "derived_from_ci", "manual", "range", "unknown"] = "unknown"
    cv_source: Optional[Literal["reported", "derived_from_ci", "manual", "range", "unknown"]] = None
    parameter: Optional[Literal["AUC", "Cmax"]] = None
    confidence: Optional[Literal["low", "medium", "high"]] = None
    confidence_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Numeric 0..1 for trust policy (N_det without human confirmation). Set in cv_gate."
    )
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


class CVRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: NumericValue
    high: NumericValue
    mode: Optional[NumericValue] = None

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    AuditTrailItem,
    CVInfo,
    CIValue,
    DataQuality,
    DesignHints,
    MealDetails,
    OpenQuestion,
    PKValue,
    RegCheckItem,
    SourceCandidate,
    StudyCondition,
)


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


class StudyDesignSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommended: Optional[str] = None
    reasoning: List[str] = Field(default_factory=list)


class StudySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: Optional[str] = None
    dosage_form: Optional[str] = None
    dose: Optional[str] = None
    protocol_id: Optional[str] = None
    design: Optional[StudyDesignSummary] = None
    fed_fasted: StudyCondition = "unknown"
    protocol_condition: Optional[Literal["fed", "fasted", "both"]] = None
    study_phase: Optional[Literal["single", "two-phase", "auto"]] = None
    washout_days: Optional[float] = None
    periods_count: Optional[int] = None
    sequences: List[str] = Field(default_factory=list)
    sampling_schedule: List[str] = Field(default_factory=list)
    total_blood_volume_ml: Optional[float] = None
    gender_requirement: Optional[str] = None
    age_range: Optional[str] = None
    additional_constraints: Optional[str] = None


class PKSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pk_values: List[PKValue] = Field(default_factory=list)
    ci_values: List[CIValue] = Field(default_factory=list)


class DQISummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: Optional[int] = None
    level: Optional[Literal["green", "yellow", "red"]] = None
    allow_n_det: Optional[bool] = None
    reasons: List[str] = Field(default_factory=list)


class CVSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Optional[str] = None
    value: Optional[float] = None
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    confidence: Optional[str] = None
    confirmed_by_human: Optional[bool] = None


class SampleSizeDetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_analysis: Optional[int] = None
    n_rand: Optional[int] = None
    n_screen: Optional[int] = None


class SampleSizeRiskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: Dict[str, int] = Field(default_factory=dict)
    mc_seed: Optional[int] = None


class SampleSizeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_det: Optional[SampleSizeDetSummary] = None
    n_risk: Optional[SampleSizeRiskSummary] = None


class RegCheckSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[RegCheckItem] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)


class SynopsisCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_fields: List[str] = Field(default_factory=list)
    missing_headings: List[str] = Field(default_factory=list)
    level: Optional[Literal["green", "yellow", "red"]] = None
    notes: List[str] = Field(default_factory=list)


class FullReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    dosage_form: Optional[str] = None
    dose: Optional[str] = None
    protocol_id: Optional[str] = None
    protocol_status: Optional[str] = None
    replacement_subjects: Optional[bool] = None
    visit_day_numbering: Optional[str] = None
    protocol_condition: Optional[Literal["fed", "fasted", "both"]] = None
    study_phase: Optional[Literal["single", "two-phase", "auto"]] = None
    gender_requirement: Optional[str] = None
    age_range: Optional[str] = None
    additional_constraints: Optional[str] = None
    sources: List[SourceCandidate] = Field(default_factory=list)
    pk_values: List[PKValue] = Field(default_factory=list)
    ci_values: List[CIValue] = Field(default_factory=list)
    study_condition: StudyCondition = "unknown"
    meal_details: Optional[MealDetails] = None
    design_hints: Optional[DesignHints] = None
    cv_info: CVInfo
    data_quality: DataQuality
    design: Optional[DesignDecision] = None
    sample_size_det: Optional[SampleSizeDet] = None
    sample_size_risk: Optional[SampleSizeRisk] = None
    reg_check: List[RegCheckItem] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    audit_trail: List[AuditTrailItem] = Field(default_factory=list)
    study: Optional[StudySummary] = None
    pk: Optional[PKSummary] = None
    dqi: Optional[DQISummary] = None
    cv: Optional[CVSummary] = None
    sample_size: Optional[SampleSizeSummary] = None
    reg_check_summary: Optional[RegCheckSummary] = None
    synopsis_completeness: Optional[SynopsisCompleteness] = None

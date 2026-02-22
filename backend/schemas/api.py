from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    CIValue,
    CVRange,
    DesignHints,
    MealDetails,
    NumericValue,
    OpenQuestion,
    PKValue,
    RegCheckItem,
    SourceCandidate,
    StudyCondition,
    ValidationIssue,
)


class SearchSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str = Field(..., description="English INN для PubMed")
    inn_ru: Optional[str] = Field(None, description="МНН на русском для отображения/контекста")
    retmax: int = Field(10, ge=1, le=50)


class SearchSourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    query: str
    sources: List[SourceCandidate]
    warnings: List[str] = Field(default_factory=list)


class TranslateInnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn_ru: str = Field(..., description="МНН на русском (кириллица)")


class TranslateInnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn_en: str = Field("", description="English INN для PubMed")
    synonyms: List[str] = Field(default_factory=list, description="Синонимы / варианты написания")


class PKExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str = Field(..., description="English INN для PubMed/контекста")
    inn_ru: Optional[str] = Field(None, description="МНН на русском для отображения")
    sources: List[str] = Field(..., description="List of PMIDs or PMCID:... identifiers")


class PKExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inn: str
    pk_values: List[PKValue]
    ci_values: List[CIValue] = Field(default_factory=list)
    study_condition: StudyCondition = "unknown"
    meal_details: Optional[MealDetails] = None
    design_hints: Optional[DesignHints] = None
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
    nti: Optional[bool] = None
    protocol_condition: Optional[Literal["fed", "fasted", "both"]] = None
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

    inn: str = Field(..., description="English INN для PubMed / DrugBank")
    inn_ru: Optional[str] = Field(None, description="МНН на русском для синопсиса / LLM")
    dosage_form: Optional[str] = Field(None, description="Лекарственная форма (e.g. таблетки, капсулы)")
    dose: Optional[str] = Field(None, description="Дозировка (e.g. 500 mg)")
    retmax: int = Field(10, ge=1, le=50)
    selected_sources: Optional[List[str]] = None
    manual_cv: Optional[float] = None
    cv_confirmed: bool = False
    rsabe_requested: Optional[bool] = Field(None, description="Явный запрос RSABE (иначе — автоопределение по CV)")
    preferred_design: Optional[str] = Field(None, description="Предпочтительный дизайн (иначе — автовыбор)")
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
    protocol_condition: Optional[Literal["fed", "fasted", "both"]] = None
    nti: Optional[bool] = None
    study_phase: Optional[Literal["single", "two-phase", "auto"]] = Field(
        None, description="Тип исследования: однопериодное / двухпериодное (БЭ) или автовыбор"
    )
    schedule_days: Optional[float] = None
    hospitalization_duration_days: Optional[float] = None
    sampling_duration_days: Optional[float] = None
    follow_up_duration_days: Optional[float] = None
    phone_follow_up_ok: Optional[bool] = None
    blood_volume_total_ml: Optional[float] = None
    blood_volume_pk_ml: Optional[float] = None
    gender_requirement: Optional[str] = Field(None, description="Гендерный состав (e.g. мужчины, женщины, оба пола)")
    age_range: Optional[str] = Field(None, description="Возрастной диапазон (e.g. 18-55)")
    additional_constraints: Optional[str] = Field(None, description="Иные ограничения заказчика")
    output_mode: Literal["draft", "final"] = Field(
        "draft",
        description="draft: only warnings/open questions, report always returned. final: 422 if blockers (policy-driven).",
    )
    final_require_n_det: bool = Field(
        True,
        description="When output_mode=final: require at least N_det or N_risk; block if both missing.",
    )
    final_require_cv_point: bool = Field(
        False,
        description="When output_mode=final: if True, block when CV point estimate missing (even with range). If False, allow risk (block only CV_absent_completely).",
    )
    final_require_primary_endpoints: bool = Field(
        True,
        description="When output_mode=final: require Cmax and AUC (from pk_values or ci_values); block if missing.",
    )

from __future__ import annotations

from typing import Optional

from backend.schemas import (
    CVInfo,
    CVInput,
    DesignDecision,
    DQISummary,
    FullReport,
    CVSummary,
    NumericValue,
    OpenQuestion,
    PKExtractionResponse,
    PKSummary,
    RegCheckSummary,
    RunPipelineRequest,
    SampleSizeDet,
    SampleSizeDetSummary,
    SampleSizeRiskSummary,
    SampleSizeSummary,
    StudyDesignSummary,
    StudySummary,
    SynopsisCompleteness,
    ValidationIssue,
)
from backend.services.cv_gate import select_cv_info
from backend.services.data_quality import compute_data_quality
from backend.services.sample_size import calc_sample_size
from backend.services.sample_size_risk import compute_sample_size_risk
from backend.services.synopsis_requirements import evaluate_synopsis_completeness
from backend.services.utils import configure_logging, now_iso


def run_pipeline(
    req: RunPipelineRequest,
    *,
    pubmed_client,
    pk_extractor,
    validator,
    design_engine,
    variability_model,
    reg_checker,
    logger=None,
) -> FullReport:
    logger = logger or configure_logging()

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

    pk_values_calc, ci_values_calc, calc_notes = filter_pk_ci_for_calculation(
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

    # 5) Design — учёт preferred_design и rsabe_requested
    _nti_for_design = req.nti
    _cv_input_for_design = _cv_input_from_cvinfo(cv_info)
    if req.rsabe_requested and _cv_input_for_design is not None:
        # Если пользователь явно запросил RSABE, а CV < 50%, поднимаем warning
        if _cv_input_for_design.cv.value < 50:
            warnings.append("RSABE requested but CVintra < 50%; engine may not trigger RSABE automatically.")
    design_resp = design_engine.select_design(pk_json_calc, _cv_input_for_design, _nti_for_design)
    _auto_design = design_resp.design
    _auto_reasoning = design_resp.reasoning_text or (
        design_resp.reasoning[0].message if design_resp.reasoning else ""
    )
    _auto_rule_id = design_resp.reasoning_rule_id or (
        design_resp.reasoning[0].rule_id if design_resp.reasoning else None
    )
    if req.preferred_design and req.preferred_design.strip():
        _chosen_design = req.preferred_design.strip()
        if _chosen_design != _auto_design:
            warnings.append(
                f"User preferred design '{_chosen_design}' overrides engine recommendation '{_auto_design}'."
            )
            _auto_reasoning = f"User override: {_chosen_design} (engine suggested {_auto_design}: {_auto_reasoning})"
            _auto_rule_id = "USER_OVERRIDE"
        _auto_design = _chosen_design
    if req.rsabe_requested and "replicate" not in _auto_design.lower():
        _auto_design = "4-way_replicate"
        _auto_reasoning = f"RSABE explicitly requested → 4-way_replicate (original: {design_resp.design})"
        _auto_rule_id = "RSABE_USER_REQUEST"
        warnings.append("Design overridden to 4-way_replicate due to explicit RSABE request.")

    design_decision = DesignDecision(
        recommendation=_auto_design,
        reasoning_rule_id=_auto_rule_id,
        reasoning_text=_auto_reasoning,
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
        protocol_condition=req.protocol_condition,
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

    study_summary = StudySummary(
        inn=req.inn,
        dosage_form=req.dosage_form,
        dose=req.dose,
        protocol_id=protocol_id,
        design=StudyDesignSummary(
            recommended=design_decision.recommendation,
            reasoning=[design_decision.reasoning_text] if design_decision.reasoning_text else [],
        ),
        fed_fasted=pk_json.study_condition if pk_json else "unknown",
        protocol_condition=req.protocol_condition,
        study_phase=req.study_phase,
        washout_days=req.schedule_days,
        periods_count=None,
        sequences=[],
        sampling_schedule=[],
        total_blood_volume_ml=req.blood_volume_total_ml,
        gender_requirement=req.gender_requirement,
        age_range=req.age_range,
        additional_constraints=req.additional_constraints,
    )
    pk_summary = PKSummary(pk_values=pk_values, ci_values=ci_values)
    dqi_summary = DQISummary(
        score=data_quality.score,
        level=data_quality.level,
        allow_n_det=data_quality.allow_n_det,
        reasons=list(data_quality.reasons or []),
    )
    cv_summary = CVSummary(
        method=cv_info.cv_source or cv_info.source,
        value=cv_info.value,
        range_low=cv_info.range_low,
        range_high=cv_info.range_high,
        confidence=cv_info.confidence,
        confirmed_by_human=cv_info.confirmed_by_user,
    )
    det_summary = None
    if sample_det:
        det_summary = SampleSizeDetSummary(
            n_analysis=sample_det.n_total,
            n_rand=sample_det.n_rand,
            n_screen=sample_det.n_screen,
        )
    risk_summary = None
    if sample_risk:
        risk_summary = SampleSizeRiskSummary(
            targets=sample_risk.n_targets,
            mc_seed=sample_risk.seed,
        )
    sample_size_summary = SampleSizeSummary(n_det=det_summary, n_risk=risk_summary)
    reg_summary = RegCheckSummary(items=reg_resp.checks, open_questions=open_questions)

    synopsis_completeness = evaluate_synopsis_completeness(
        {
            "inn": req.inn,
            "protocol_id": protocol_id,
            "design": {"recommendation": design_decision.recommendation},
            "pk_values": pk_values,
            "ci_values": ci_values,
            "sample_size_det": sample_det,
            "sample_size_risk": sample_risk,
            "data_quality": data_quality.model_dump(),
            "reg_check": reg_resp.checks,
            "open_questions": open_questions,
            "study": study_summary.model_dump(),
            "pk": pk_summary.model_dump(),
            "dqi": dqi_summary.model_dump(),
            "cv": cv_summary.model_dump(),
            "sample_size": sample_size_summary.model_dump(),
            "reg_check_summary": reg_summary.model_dump(),
        }
    )

    return FullReport(
        inn=req.inn,
        dosage_form=req.dosage_form,
        dose=req.dose,
        protocol_id=protocol_id,
        protocol_status=protocol_status,
        replacement_subjects=req.replacement_subjects,
        visit_day_numbering=req.visit_day_numbering,
        protocol_condition=req.protocol_condition,
        study_phase=req.study_phase,
        gender_requirement=req.gender_requirement,
        age_range=req.age_range,
        additional_constraints=req.additional_constraints,
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
        study=study_summary,
        pk=pk_summary,
        dqi=dqi_summary,
        cv=cv_summary,
        sample_size=sample_size_summary,
        reg_check_summary=reg_summary,
        synopsis_completeness=SynopsisCompleteness(**synopsis_completeness),
    )


def filter_pk_ci_for_calculation(
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


def _resolve_protocol_id(protocol_id: Optional[str], inn: str) -> tuple[str, str]:
    if protocol_id and protocol_id.strip():
        return protocol_id.strip(), "Final"
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

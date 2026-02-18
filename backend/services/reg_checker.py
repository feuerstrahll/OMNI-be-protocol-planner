from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import os
import re
import yaml

from backend.schemas import (
    CVInfo,
    CVInput,
    DataQuality,
    OpenQuestion,
    PKExtractionResponse,
    RegCheckItem,
    RegCheckResponse,
    ValidationIssue,
)


class RegChecker:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}
        self._templates = _load_open_question_templates("docs/OPEN_QUESTIONS_LIBRARY.md")
        self._question_meta = self._load_question_meta()

    def run(
        self,
        design: str,
        pk_json: PKExtractionResponse,
        schedule_days: Optional[float],
        cv_input: Optional[CVInput],
        hospitalization_duration_days: Optional[float] = None,
        sampling_duration_days: Optional[float] = None,
        follow_up_duration_days: Optional[float] = None,
        phone_follow_up_ok: Optional[bool] = None,
        blood_volume_total_ml: Optional[float] = None,
        blood_volume_pk_ml: Optional[float] = None,
        *,
        data_quality: Optional[DataQuality] = None,
        cv_info: Optional[CVInfo] = None,
        validation_issues: Optional[List[ValidationIssue]] = None,
    ) -> RegCheckResponse:
        checks: List[RegCheckItem] = []

        context = self._build_context(design, pk_json, schedule_days, cv_input, cv_info=cv_info)

        checks.extend(self._check_cv_design(context))
        checks.extend(self._check_washout(context))
        checks.extend(self._check_required_pk(pk_json))
        checks.extend(
            self._check_missing_inputs(
                hospitalization_duration_days=hospitalization_duration_days,
                sampling_duration_days=sampling_duration_days,
                follow_up_duration_days=follow_up_duration_days,
                phone_follow_up_ok=phone_follow_up_ok,
                blood_volume_total_ml=blood_volume_total_ml,
                blood_volume_pk_ml=blood_volume_pk_ml,
            )
        )

        checks.extend(self._dynamic_checks(data_quality, cv_info))

        checks = _dedupe_checks(checks)
        val_issues = validation_issues or pk_json.validation_issues or []
        open_questions = self._build_open_questions(checks, val_issues)
        open_questions = _dedupe_open_questions(open_questions)

        return RegCheckResponse(checks=checks, open_questions=open_questions)

    def _build_context(
        self,
        design: str,
        pk_json: PKExtractionResponse,
        schedule_days: Optional[float],
        cv_input: Optional[CVInput],
        *,
        cv_info: Optional[CVInfo] = None,
    ) -> Dict[str, object]:
        cv_value, cv_confirmed = _resolve_cv_value(pk_json, cv_input, cv_info)
        t_half = _extract_pk_value(pk_json, "t1/2")
        return {
            "design": design,
            "cv_value": cv_value,
            "cv_confirmed": cv_confirmed,
            "t_half": t_half,
            "schedule_days": schedule_days,
        }

    def _check_cv_design(self, context: Dict[str, object]) -> List[RegCheckItem]:
        cfg = _find_check(self.rules.get("checks", []), "CV_HIGH_DESIGN")
        if not cfg:
            return []

        design = str(context.get("design") or "")
        cv_value = context.get("cv_value")
        cv_confirmed = bool(context.get("cv_confirmed"))
        threshold = float(cfg.get("cv_threshold", 50))
        keywords = [str(k).lower() for k in (cfg.get("replicate_keywords") or [])]
        design_is_replicate = any(k in design.lower() for k in keywords)

        if cv_value is None:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="CLARIFY",
                    message=cfg.get("message_missing_cv", "CVintra not available."),
                    what_to_clarify=[cfg.get("clarify_missing_cv", "Provide CVintra.")],
                )
            ]

        if not cv_confirmed:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="CLARIFY",
                    message=cfg.get("message_unconfirmed", "CVintra provided but not confirmed."),
                    what_to_clarify=[cfg.get("clarify_unconfirmed", "Confirm CVintra value.")],
                )
            ]

        if float(cv_value) > threshold and not design_is_replicate:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="RISK",
                    message=cfg.get("message_risk", "High CVintra detected but design is not replicate/scaled."),
                    what_to_clarify=[cfg.get("clarify_risk", "Consider replicate design or scaled BE approach.")],
                )
            ]

        return [
            RegCheckItem(
                rule_id=cfg.get("id"),
                status="OK",
                message=cfg.get("message_ok", "Design aligns with CVintra risk profile."),
            )
        ]

    def _check_washout(self, context: Dict[str, object]) -> List[RegCheckItem]:
        cfg = _find_check(self.rules.get("checks", []), "WASHOUT")
        if not cfg:
            return []

        schedule_days = context.get("schedule_days")
        t_half = context.get("t_half")
        multiplier = float(cfg.get("multiplier", 5))

        if schedule_days is None:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="CLARIFY",
                    message=cfg.get("message_missing_schedule", "Washout duration not provided."),
                    what_to_clarify=[cfg.get("clarify_missing_schedule", "Provide washout duration.")],
                )
            ]

        if t_half is None:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="CLARIFY",
                    message=cfg.get("message_missing_half", "t1/2 not available to validate washout duration."),
                    what_to_clarify=[cfg.get("clarify_missing_half", "Provide t1/2.")],
                )
            ]

        required_days = multiplier * float(t_half) / 24.0
        if float(schedule_days) < required_days:
            return [
                RegCheckItem(
                    rule_id=cfg.get("id"),
                    status="RISK",
                    message=cfg.get("message_risk", "Washout may be shorter than 5x t1/2."),
                    what_to_clarify=[f"Recommended >= {required_days:.1f} days based on t1/2."],
                )
            ]

        return [
            RegCheckItem(
                rule_id=cfg.get("id"),
                status="OK",
                message=cfg.get("message_ok", "Washout duration appears adequate."),
            )
        ]

    def _check_required_pk(self, pk_json: PKExtractionResponse) -> List[RegCheckItem]:
        required_cfg = (self.rules.get("required_pk") or {}).get("decision_85") or {}
        required = required_cfg.get("parameters") or []
        rule_id = required_cfg.get("id", "DEC85_REQUIRED_PK")
        message = required_cfg.get("message", "Missing required PK parameters (Decision 85).")
        clarify = required_cfg.get("clarify_text", "Provide missing PK parameters required by Decision 85.")

        present = {pk.name for pk in pk_json.pk_values}
        missing: List[str] = []
        base_required = [p for p in required if p not in ("t1/2", "lambda_z")]
        for param in base_required:
            if param not in present:
                missing.append(param)
        has_half = ("t1/2" in present) or ("lambda_z" in present)
        if not has_half and ("t1/2" in required or "lambda_z" in required):
            missing.append("t1/2 or lambda_z")

        if missing:
            detail = f"Missing: {', '.join(missing)}."
            return [
                RegCheckItem(
                    rule_id=rule_id,
                    status="CLARIFY",
                    message=f"{message} {detail}",
                    what_to_clarify=[clarify],
                )
            ]
        return []

    def _check_missing_inputs(self, **inputs: Optional[float | bool]) -> List[RegCheckItem]:
        checks: List[RegCheckItem] = []
        for rule in self.rules.get("open_questions", []) or []:
            rule_id = rule.get("id")
            fields = rule.get("input_fields") or []
            missing = [f for f in fields if inputs.get(f) is None]
            if not missing:
                continue
            clarify = rule.get("clarify_message") or rule.get("question") or "Clarify missing information."
            checks.append(
                RegCheckItem(
                    rule_id=rule_id,
                    status="CLARIFY",
                    message=rule.get("message") or rule.get("question") or "Missing required information.",
                    what_to_clarify=[clarify],
                )
            )
        return checks

    def _dynamic_checks(
        self,
        data_quality: Optional[DataQuality],
        cv_info: Optional[CVInfo],
    ) -> List[RegCheckItem]:
        checks: List[RegCheckItem] = []
        if data_quality and data_quality.level == "red":
            checks.append(
                RegCheckItem(
                    rule_id="DQI_RED",
                    status="CLARIFY",
                    message="Data quality is red; confirm sources / provide stronger BE/PK evidence.",
                    what_to_clarify=["Confirm sources / provide stronger BE/PK evidence."],
                )
            )

        if cv_info and cv_info.cv_source == "derived_from_ci":
            checks.append(
                RegCheckItem(
                    rule_id="CV_DERIVED_ASSUMPTIONS",
                    status="CLARIFY",
                    message="CV derived from CI; confirm assumptions (90% CI, 2x2 crossover, log-scale, n/CI correctness).",
                    what_to_clarify=[
                        "Confirm assumptions: 90% CI, 2x2 crossover, log-scale, correctness of n/CI."
                    ],
                )
            )

        if cv_info and cv_info.cv_source == "range":
            checks.append(
                RegCheckItem(
                    rule_id="CV_RANGE_UNCERTAIN",
                    status="CLARIFY",
                    message="CV from range; provide measured CVintra if possible; risk-based N used.",
                    what_to_clarify=["Provide measured CVintra if possible; risk-based N used."],
                )
            )

        if cv_info and not cv_info.confirmed_by_user:
            checks.append(
                RegCheckItem(
                    rule_id="CV_CONFIRM_NDET",
                    status="CLARIFY",
                    message="Confirm CV to enable N_det calculation.",
                    what_to_clarify=["Confirm CV to enable N_det calculation."],
                )
            )

        return checks

    def _build_open_questions(
        self,
        checks: List[RegCheckItem],
        validation_issues: List[ValidationIssue],
    ) -> List[OpenQuestion]:
        questions: List[OpenQuestion] = []
        for check in checks:
            if check.status != "CLARIFY":
                continue
            template = self._templates.get(check.rule_id or "")
            if template:
                question_text = template
            elif check.what_to_clarify:
                question_text = check.what_to_clarify[0]
            else:
                question_text = check.message
            category, priority = self._meta_for_rule(check.rule_id)
            questions.append(
                OpenQuestion(
                    category=category,
                    question=question_text,
                    priority=priority,
                    linked_rule_id=check.rule_id,
                )
            )

        for issue in validation_issues:
            metric = issue.metric or "PK value"
            question_text = f"Resolve validation {issue.severity.lower()} for {metric}: {issue.message}"
            priority = "high" if issue.severity == "ERROR" else "medium"
            questions.append(
                OpenQuestion(
                    category="validation",
                    question=question_text,
                    priority=priority,
                    linked_rule_id=f"VALIDATION_{metric}",
                )
            )

        return questions

    def _load_question_meta(self) -> Dict[str, Dict[str, str]]:
        meta: Dict[str, Dict[str, str]] = {}
        for item in self.rules.get("open_questions", []) or []:
            if not item.get("id"):
                continue
            meta[item["id"]] = {
                "category": item.get("category") or "general",
                "priority": item.get("priority") or "medium",
            }

        required_cfg = (self.rules.get("required_pk") or {}).get("decision_85") or {}
        if required_cfg.get("id"):
            meta[required_cfg.get("id")] = {
                "category": required_cfg.get("category") or "general",
                "priority": required_cfg.get("priority") or "medium",
            }

        for rule_id, values in (self.rules.get("question_meta") or {}).items():
            if rule_id:
                meta[rule_id] = {
                    "category": values.get("category") or meta.get(rule_id, {}).get("category") or "general",
                    "priority": values.get("priority") or meta.get(rule_id, {}).get("priority") or "medium",
                }

        return meta

    def _meta_for_rule(self, rule_id: Optional[str]) -> Tuple[str, str]:
        if not rule_id:
            return "general", "medium"
        meta = self._question_meta.get(rule_id) or {}
        return meta.get("category") or "general", meta.get("priority") or "medium"


def _resolve_cv_value(
    pk_json: PKExtractionResponse,
    cv_input: Optional[CVInput],
    cv_info: Optional[CVInfo],
) -> Tuple[Optional[float], bool]:
    if cv_info is not None:
        return cv_info.value, bool(cv_info.confirmed_by_user)
    if cv_input is not None:
        return cv_input.cv.value, bool(cv_input.confirmed)
    cv_from_pk = _extract_pk_value(pk_json, "CVintra")
    return cv_from_pk, False


def _extract_pk_value(pk_json: PKExtractionResponse, name: str) -> Optional[float]:
    for pk in pk_json.pk_values:
        if pk.name == name and pk.value is not None:
            return pk.value
    return None


def _find_check(checks: List[dict], rule_id: str) -> Optional[dict]:
    for item in checks:
        if item.get("id") == rule_id:
            return item
    return None


def _load_open_question_templates(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except Exception:
        return {}

    templates: Dict[str, str] = {}
    current_id: Optional[str] = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("- "):
            current_id = line[2:].strip()
            continue
        if current_id and line.lower().startswith("question:"):
            question = line.split(":", 1)[1].strip().strip('"')
            templates[current_id] = question
    return templates


def _dedupe_checks(checks: List[RegCheckItem]) -> List[RegCheckItem]:
    seen = set()
    deduped: List[RegCheckItem] = []
    for check in checks:
        key = (check.rule_id, check.status, check.message)
        if key in seen:
            continue
        deduped.append(check)
        seen.add(key)
    return deduped


def _dedupe_open_questions(questions: List[OpenQuestion]) -> List[OpenQuestion]:
    seen = set()
    deduped: List[OpenQuestion] = []
    for q in questions:
        key = re.sub(r"\s+", " ", q.question.strip().lower())
        if key in seen:
            continue
        deduped.append(q)
        seen.add(key)
    return deduped

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
        self._rules_list = self.rules.get("rules") or []
        self._use_generic_rules = bool(self._rules_list)

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
        nti: Optional[bool] = None,
    ) -> RegCheckResponse:
        checks: List[RegCheckItem] = []

        if self._use_generic_rules:
            context = self._build_generic_context(
                design,
                pk_json,
                schedule_days,
                cv_input,
                data_quality=data_quality,
                cv_info=cv_info,
                validation_issues=validation_issues,
                nti=nti,
            )
            checks.extend(self._evaluate_generic_rules(context))
        else:
            context = self._build_context(design, pk_json, schedule_days, cv_input, cv_info=cv_info)
            checks.extend(self._check_cv_design(context))
            checks.extend(self._check_washout(context))
            checks.extend(self._check_required_pk(pk_json))
        checks.extend(self._check_feeding_conflict(pk_json))
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

        checks.extend(self._dynamic_checks(data_quality, cv_info, use_generic_rules=self._use_generic_rules))

        checks = _dedupe_checks(checks)
        val_issues = validation_issues or pk_json.validation_issues or []
        open_questions = self._build_open_questions(checks, val_issues)
        open_questions = _dedupe_open_questions(open_questions)

        return RegCheckResponse(checks=checks, open_questions=open_questions)

    def _check_feeding_conflict(self, pk_json: PKExtractionResponse) -> List[RegCheckItem]:
        if not pk_json.warnings or "feeding_condition_conflict" not in pk_json.warnings:
            return []
        return [
            RegCheckItem(
                rule_id="FEEDING_CONDITION_CLARIFY",
                status="CLARIFY",
                message=(
                    "Source contains both FED and FASTED results. Choose protocol condition (FED or FASTED) "
                    "before using PK/CI for calculations."
                ),
                what_to_clarify=[
                    "Select protocol condition: fed/fasted",
                    "Confirm which PK/CI values correspond to the chosen condition",
                ],
            )
        ]

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

    def _build_generic_context(
        self,
        design: str,
        pk_json: PKExtractionResponse,
        schedule_days: Optional[float],
        cv_input: Optional[CVInput],
        *,
        data_quality: Optional[DataQuality],
        cv_info: Optional[CVInfo],
        validation_issues: Optional[List[ValidationIssue]],
        nti: Optional[bool],
    ) -> Dict[str, object]:
        warnings = self._collect_warning_codes(pk_json, validation_issues or [])
        t_half = self._extract_pk_value(pk_json, "t1/2", ignore_ambiguous=True)
        auc_name = self._first_metric_name(pk_json, "AUC")
        cv_value, cv_confirmed = _resolve_cv_value(pk_json, cv_input, cv_info)
        if cv_info:
            cv_source = cv_info.cv_source or cv_info.source
        elif cv_input:
            cv_source = "manual"
        else:
            cv_source = None
        if cv_source == "range":
            cv_source = "variability_range"
        cv_ratio = None
        if cv_value is not None:
            try:
                cv_ratio = float(cv_value) / 100.0
            except Exception:
                cv_ratio = None

        ci_candidate = self._select_ci_candidate(pk_json.ci_values)
        ci_low = ci_high = None
        ci_n = None
        ci_design = None
        ci_log = None
        if ci_candidate is not None:
            ci_low = self._ci_ratio(ci_candidate.ci_low, ci_candidate.ci_type)
            ci_high = self._ci_ratio(ci_candidate.ci_high, ci_candidate.ci_type)
            ci_n = ci_candidate.n
            ci_design = ci_candidate.design_hint
        if pk_json.design_hints and pk_json.design_hints.log_transform is not None:
            ci_log = bool(pk_json.design_hints.log_transform)

        study_condition = pk_json.study_condition
        if pk_json.warnings and "feeding_condition_conflict" in pk_json.warnings:
            study_condition = "both"

        context = {
            "data_quality": {
                "score": data_quality.score if data_quality else None,
                "level": data_quality.level if data_quality else None,
            },
            "pk": {
                "auc": {"exists": self._has_pk(pk_json, "AUC"), "parameter_name": auc_name},
                "cmax": {"exists": self._has_pk(pk_json, "Cmax")},
                "t12_hours": t_half,
            },
            "cv": {
                "cvintra": {
                    "value": cv_ratio,
                    "parameter": None,
                    "source": cv_source,
                    "confirmed_by_human": bool(cv_confirmed),
                },
                "derived_from_ci": {
                    "ci90_low": ci_low,
                    "ci90_high": ci_high,
                    "n_total": ci_n,
                    "design": ci_design,
                    "log_scale_assumed": ci_log,
                    "assumptions_confirmed_by_human": bool(cv_confirmed) if cv_source == "derived_from_ci" else None,
                },
            },
            "drug": {"narrow_therapeutic_index": nti},
            "study": {
                "design": {"recommended": self._normalize_design_label(design)},
                "fed_fasted": study_condition,
            },
            "schedule_days": schedule_days,
            "warnings": warnings,
        }
        return context

    def _evaluate_generic_rules(self, context: Dict[str, object]) -> List[RegCheckItem]:
        checks: List[RegCheckItem] = []
        defaults = self.rules.get("defaults") or {}
        default_decision = defaults.get("decision", "OK")
        for rule in self._rules_list:
            when = rule.get("when") or {}
            if when and not self._eval_condition(when, context):
                continue
            checks.append(
                RegCheckItem(
                    rule_id=rule.get("id"),
                    status=rule.get("decision") or default_decision,
                    message=rule.get("message") or "Regulatory check fired.",
                    what_to_clarify=rule.get("what_to_clarify") or [],
                )
            )
        return checks

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

    def _collect_warning_codes(
        self,
        pk_json: PKExtractionResponse,
        validation_issues: List[ValidationIssue],
    ) -> List[str]:
        warnings: List[str] = []
        warnings.extend(pk_json.warnings or [])

        numeric_items = list(pk_json.pk_values) + list(pk_json.ci_values)
        missing_evidence = False
        missing_source = False
        for item in numeric_items:
            value = getattr(item, "value", None)
            if value is None and hasattr(item, "ci_low"):
                value = getattr(item, "ci_low", None)
            if value is None:
                continue
            evidence = getattr(item, "evidence", None) or []
            if not evidence:
                missing_evidence = True
                continue
            has_source = any(
                (ev.pmid_or_url or ev.pmid or ev.url or ev.source_id)
                for ev in evidence
                if ev is not None
            )
            if not has_source:
                missing_source = True

        if missing_evidence:
            warnings.append("missing_evidence")
        if missing_source:
            warnings.append("missing_source")

        for pk in pk_json.pk_values:
            for warn in pk.warnings or []:
                if warn == "missing_unit":
                    warnings.append("unit_missing")
                if warn == "unit_not_allowed":
                    warnings.append("unit_suspect")
                if warn == "unit_normalization_failed":
                    warnings.append("suspicious_conversion")
                if "conflict_detected" in warn:
                    warnings.append("conflicting_values")

        for issue in validation_issues:
            if "conflict" in issue.message.lower():
                warnings.append("conflicting_values")

        return list(dict.fromkeys(warnings))

    @staticmethod
    def _first_metric_name(pk_json: PKExtractionResponse, prefix: str) -> Optional[str]:
        for pk in pk_json.pk_values:
            if pk.name and pk.name.upper().startswith(prefix.upper()):
                return pk.name
        return None

    @staticmethod
    def _has_pk(pk_json: PKExtractionResponse, name: str) -> bool:
        for pk in pk_json.pk_values:
            if pk.name == name or (name == "AUC" and pk.name.upper().startswith("AUC")):
                if pk.value is not None and not getattr(pk, "ambiguous_condition", False):
                    return True
        return False

    @staticmethod
    def _extract_pk_value(
        pk_json: PKExtractionResponse, name: str, *, ignore_ambiguous: bool = False
    ) -> Optional[float]:
        for pk in pk_json.pk_values:
            if pk.name == name and pk.value is not None:
                if ignore_ambiguous and getattr(pk, "ambiguous_condition", False):
                    continue
                return pk.value
        return None

    @staticmethod
    def _select_ci_candidate(ci_values: List) -> Optional[object]:
        for ci in ci_values:
            if getattr(ci, "ambiguous_condition", False):
                continue
            return ci
        return None

    @staticmethod
    def _ci_ratio(value: Optional[float], ci_type: str) -> Optional[float]:
        if value is None:
            return None
        try:
            val = float(value)
        except Exception:
            return None
        if ci_type == "percent" or val > 2.0:
            return val / 100.0
        return val

    @staticmethod
    def _normalize_design_label(design: str) -> Optional[str]:
        text = (design or "").lower()
        if "replicate" in text:
            return "replicate"
        if "parallel" in text:
            return "parallel"
        if "2x2" in text or "2?2" in text:
            return "2x2"
        return design or None

    def _eval_condition(self, cond: dict, context: Dict[str, object]) -> bool:
        if not cond:
            return True
        if "all" in cond:
            return all(self._eval_condition(item, context) for item in cond.get("all") or [])
        if "any" in cond:
            return any(self._eval_condition(item, context) for item in cond.get("any") or [])

        field = cond.get("field")
        op = cond.get("op")
        target = cond.get("value")
        value = self._get_path(context, field) if field else None

        if op == "exists":
            return value is not None
        if op == "not_exists":
            return value is None
        if op == "truthy":
            return bool(value)
        if op == "falsy":
            return not bool(value)
        if op == "contains":
            if isinstance(value, list):
                return target in value
            if isinstance(value, str):
                return str(target) in value
            return False
        if op == "in":
            return value in (target or [])
        if op == "not_in":
            return value not in (target or [])
        if value is None:
            return False
        if op == "eq":
            return value == target
        if op == "ne":
            return value != target
        try:
            val_num = float(value)
            tgt_num = float(target)
        except Exception:
            return False
        if op == "gt":
            return val_num > tgt_num
        if op == "gte":
            return val_num >= tgt_num
        if op == "lt":
            return val_num < tgt_num
        if op == "lte":
            return val_num <= tgt_num
        return False

    @staticmethod
    def _get_path(data: Dict[str, object], path: Optional[str]) -> Optional[object]:
        if not path:
            return None
        current: object = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

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
        *,
        use_generic_rules: bool = False,
    ) -> List[RegCheckItem]:
        checks: List[RegCheckItem] = []
        if data_quality and data_quality.level == "red" and not use_generic_rules:
            checks.append(
                RegCheckItem(
                    rule_id="DQI_RED",
                    status="CLARIFY",
                    message="Data quality is red; confirm sources / provide stronger BE/PK evidence.",
                    what_to_clarify=["Confirm sources / provide stronger BE/PK evidence."],
                )
            )

        if cv_info and cv_info.cv_source == "derived_from_ci" and not use_generic_rules:
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
        match = re.search(r"(OQ-[0-9]+)", line)
        if line.startswith("###") and match:
            current_id = match.group(1)
            continue
        if current_id and line.lower().startswith("question:"):
            question = line.split(":", 1)[1].strip().strip('"')
            templates[current_id] = question
            continue
        if current_id and "question" in line.lower() and ":" in line:
            if line.lower().startswith("- **question**"):
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

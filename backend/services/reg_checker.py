from __future__ import annotations

from typing import List, Optional

import yaml

from backend.schemas import CVInput, PKExtractionResponse, RegCheckItem, RegCheckResponse


class RegChecker:
    def __init__(self, rules_path: str) -> None:
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f) or {}

    def run(
        self,
        design: str,
        pk_json: PKExtractionResponse,
        schedule_days: Optional[float],
        cv_input: Optional[CVInput],
    ) -> RegCheckResponse:
        checks: List[RegCheckItem] = []

        cv_value = None
        if cv_input and cv_input.confirmed:
            cv_value = cv_input.cv.value
        elif cv_input and not cv_input.confirmed:
            checks.append(
                RegCheckItem(
                    id="CV_CONFIRM",
                    status="CLARIFY",
                    message="CVintra provided but not confirmed.",
                    what_to_clarify="Confirm CVintra value before finalizing design.",
                )
            )
        else:
            for pk in pk_json.pk_values:
                if pk.metric == "CVintra":
                    cv_value = pk.value.value
                    break

        if cv_value is None:
            checks.append(
                RegCheckItem(
                    id="CV_AVAILABLE",
                    status="CLARIFY",
                    message="CVintra not available for regulatory assessment.",
                    what_to_clarify="Provide CVintra or justify variability assumptions.",
                )
            )
        elif cv_value > 50 and "replicate" not in design.lower():
            checks.append(
                RegCheckItem(
                    id="CV_HIGH_DESIGN",
                    status="RISK",
                    message="High CVintra detected but design is not replicate/scaled.",
                    what_to_clarify="Consider replicate design or scaled BE approach.",
                )
            )
        else:
            checks.append(
                RegCheckItem(
                    id="CV_HIGH_DESIGN",
                    status="OK",
                    message="Design aligns with CVintra risk profile.",
                )
            )

        t_half = None
        for pk in pk_json.pk_values:
            if pk.metric == "t1/2":
                t_half = pk.value.value
                break

        if schedule_days is None:
            checks.append(
                RegCheckItem(
                    id="WASHOUT",
                    status="CLARIFY",
                    message="Washout duration not provided.",
                    what_to_clarify="Provide washout duration to assess 5x t1/2 rule.",
                )
            )
        elif t_half is None:
            checks.append(
                RegCheckItem(
                    id="WASHOUT",
                    status="CLARIFY",
                    message="t1/2 not available to validate washout duration.",
                    what_to_clarify="Provide t1/2 or justify washout duration.",
                )
            )
        else:
            required = 5 * t_half / 24.0
            if schedule_days < required:
                checks.append(
                    RegCheckItem(
                        id="WASHOUT",
                        status="RISK",
                        message="Washout may be shorter than 5x t1/2.",
                        what_to_clarify=f"Recommended >= {required:.1f} days based on t1/2.",
                    )
                )
            else:
                checks.append(
                    RegCheckItem(
                        id="WASHOUT",
                        status="OK",
                        message="Washout duration appears adequate.",
                    )
                )

        return RegCheckResponse(checks=checks)

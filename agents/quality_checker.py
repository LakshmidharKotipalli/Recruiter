"""Pure-Python quality checker — evaluates shortlist adequacy and recommends next action."""

from typing import Literal
from pydantic import BaseModel
from config import APP_CONFIG


class QualityAssessment(BaseModel):
    recommended_action: Literal[
        "accept", "refine_broader", "refine_location_relaxed", "force_accept"
    ]
    reason: str
    accept: bool  # True when pipeline should terminate


class QualityChecker:
    """Evaluates pipeline output quality and recommends whether to continue or refine."""

    def __init__(self):
        cfg = APP_CONFIG.pipeline
        self.min_accepted = cfg.min_accepted
        self.max_iterations = cfg.max_iterations
        self.min_raw_per_iter = cfg.min_raw_per_iter

    def assess(
        self,
        accepted_count: int,
        close_count: int,
        total_raw: int,
        iteration: int,
        jd_quality_score: int = 10,
    ) -> QualityAssessment:
        """
        Decision logic:

        1. At max iterations → accept (or force_accept if accepted=0)
        2. accepted >= min_accepted → accept
        3. total_raw < min_raw_per_iter * iteration → refine_broader (pool too thin)
        4. close > accepted*3 AND total_raw sufficient → refine_location_relaxed
        5. else → refine_broader
        """
        at_max = iteration >= self.max_iterations

        # Hard cap — must terminate
        if at_max:
            if accepted_count == 0:
                return QualityAssessment(
                    recommended_action="force_accept",
                    reason=(
                        f"Reached max iterations ({self.max_iterations}) with 0 accepted candidates. "
                        "Promoting top close candidates as accepted."
                    ),
                    accept=True,
                )
            return QualityAssessment(
                recommended_action="accept",
                reason=(
                    f"Reached max iterations ({self.max_iterations}). "
                    f"Accepting current shortlist of {accepted_count} candidates."
                ),
                accept=True,
            )

        # Good enough shortlist
        if accepted_count >= self.min_accepted:
            return QualityAssessment(
                recommended_action="accept",
                reason=(
                    f"{accepted_count} accepted candidates meets the minimum of {self.min_accepted}."
                ),
                accept=True,
            )

        # Pool too thin — need more raw profiles
        min_raw_expected = self.min_raw_per_iter * iteration
        if total_raw < min_raw_expected:
            return QualityAssessment(
                recommended_action="refine_broader",
                reason=(
                    f"Only {total_raw} raw profiles after {iteration} iteration(s) "
                    f"(expected ≥{min_raw_expected}). Broadening search to fill the pool."
                ),
                accept=False,
            )

        # Many close but failing location — likely location gate is too strict
        if close_count > accepted_count * 3 and total_raw >= min_raw_expected:
            return QualityAssessment(
                recommended_action="refine_location_relaxed",
                reason=(
                    f"{close_count} close candidates vs {accepted_count} accepted. "
                    "High close-to-accept ratio suggests location filter is too restrictive."
                ),
                accept=False,
            )

        # Default — broaden
        return QualityAssessment(
            recommended_action="refine_broader",
            reason=(
                f"Only {accepted_count} accepted candidates after iteration {iteration}. "
                "Broadening search with expanded skill synonyms and relaxed seniority."
            ),
            accept=False,
        )

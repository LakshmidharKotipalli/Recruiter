from pydantic import BaseModel, Field
from typing import Any, Literal, Optional, List


class WeightConfig(BaseModel):
    must_have_skills: float = Field(0.50, ge=0, le=1)
    nice_to_have_skills: float = Field(0.25, ge=0, le=1)
    seniority_level: float = Field(0.15, ge=0, le=1)
    location_match: float = Field(0.10, ge=0, le=1)

    def normalized(self) -> "WeightConfig":
        """Return weights normalized to sum to 1.0."""
        total = (
            self.must_have_skills
            + self.nice_to_have_skills
            + self.seniority_level
            + self.location_match
        )
        if total == 0:
            return WeightConfig()
        return WeightConfig(
            must_have_skills=self.must_have_skills / total,
            nice_to_have_skills=self.nice_to_have_skills / total,
            seniority_level=self.seniority_level / total,
            location_match=self.location_match / total,
        )


class HardRejectRules(BaseModel):
    min_years_experience: Optional[int] = None
    required_locations: list[str] = []
    excluded_locations: list[str] = []


class ThresholdConfig(BaseModel):
    accepted_min_score: float = 0.70
    close_min_score: float = 0.50
    min_results_per_source: int = 5


class RecruiterConfig(BaseModel):
    weights: WeightConfig = WeightConfig()
    hard_reject_rules: HardRejectRules = HardRejectRules()
    thresholds: ThresholdConfig = ThresholdConfig()


# Layer 01 Input
class PipelineInput(BaseModel):
    raw_jd: str
    config: RecruiterConfig = RecruiterConfig()


# Layer 02 Output — SearchSpec
class SearchSpec(BaseModel):
    role_title: str
    core_requirements: str = ""          # 2-3 sentence plain-English role summary
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    seniority_level: str                 # "junior" | "mid" | "senior" | "lead" | "principal"
    location: str
    weights: WeightConfig
    gap_flags: list[str] = []            # warnings from gap detector / unrealistic combos
    # JD analysis artefacts
    cleaned_jd: str = ""                 # rewritten JD when original was poor
    jd_quality_score: int = 10           # 0–10 (10 = perfectly structured)
    jd_issues: list[str] = []            # problems detected in the original JD
    inferred_skills: list[str] = []      # skills added via role-knowledge gap fill


# Layer 03 Output — QuerySet
class SourceQueries(BaseModel):
    primary: list[str]
    fallback: list[str]


class QuerySet(BaseModel):
    google_cse: SourceQueries
    naukri: SourceQueries
    pdl: SourceQueries


# Layer 04 — RawProfile
class Experience(BaseModel):
    title: str
    company: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None


class Education(BaseModel):
    institution: str
    degree: str
    field: Optional[str] = None
    year: Optional[int] = None


class RawProfile(BaseModel):
    name: str
    title: str
    company: str
    skills: list[str]
    experience: list[Experience]
    education: list[Education]
    source: str  # "google_cse" | "naukri" | "pdl"
    profile_url: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None


# Layer 05 — DedupedProfile
class AltProfile(BaseModel):
    source: str
    url: Optional[str] = None
    field_diffs: dict[str, Any] = {}


class DedupedProfile(BaseModel):
    name: str
    title: str
    company: str
    skills: list[str]
    experience: list[Experience]
    education: list[Education]
    location: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    primary_source: str
    primary_url: Optional[str] = None
    alt_profiles: list[AltProfile] = []
    merged_fields: dict[str, str] = {}  # field -> source that won


# Layer 06 — ScoredProfile
class ScoredProfile(BaseModel):
    profile: DedupedProfile
    score_pct: float  # 0.0–1.0
    matched: list[str]  # skills/criteria that matched
    gaps: list[str]  # skills/criteria that were missing
    reasoning: str  # 1-2 sentence NL explanation
    bucket: Literal["accepted", "close", "rejected"]


# Execution trace models (agentic pipeline)
class TraceStep(BaseModel):
    tool: str
    iteration: int
    input_keys: List[str]
    result_summary: str


class ExecutionTrace(BaseModel):
    steps: List[TraceStep] = []
    total_iterations: int = 1
    total_llm_calls: int = 0
    refinement_reasons: List[str] = []
    hitl_decisions: List[str] = []  # plain-English record of what recruiter decided/changed
    final_action: str = "accept"


class HITLDecision(BaseModel):
    """Body of POST /api/run/{run_id}/resume."""
    checkpoint: Literal["jd_review", "profile_count"]
    spec_overrides: dict = {}   # for jd_review: fields to patch on SearchSpec
    decision: str = "proceed"   # for profile_count: "broaden"|"relax_location"|"proceed"


# Layer 07 — Output
class PipelineOutput(BaseModel):
    accepted: list[ScoredProfile]
    close: list[ScoredProfile]
    rejected_count: int
    source_availability: dict[str, bool]  # source -> succeeded
    search_spec: SearchSpec
    total_raw: int
    total_deduped: int
    execution_trace: Optional[ExecutionTrace] = None  # None in legacy pipeline paths

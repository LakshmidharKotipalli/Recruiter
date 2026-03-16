import logging
from agents.base import BaseAgent
from config import ModelConfig
from schemas.models import (
    DedupedProfile,
    SearchSpec,
    RecruiterConfig,
    ScoredProfile,
    WeightConfig,
)

logger = logging.getLogger(__name__)

SENIORITY_LEVELS = ["junior", "mid", "senior", "lead", "principal"]

SYSTEM_PROMPT_REASONING = """You are a technical recruiter writing brief candidate assessments.
Given a list of candidate profiles and a job spec, write a 1-2 sentence plain-language reasoning for each candidate explaining why they are a good or partial fit.
Be specific: mention key matching skills and any notable gaps.
Return ONLY valid JSON array with objects containing exactly two fields: "profile_url" and "reasoning".
No prose, no markdown fences."""


class RankingAgent(BaseAgent):
    """Sub-Agent 05: Ranking & Bucketing Agent."""

    def __init__(self, config: ModelConfig):
        super().__init__(config)

    def rank(
        self,
        profiles: list[DedupedProfile],
        search_spec: SearchSpec,
        recruiter_config: RecruiterConfig,
    ) -> list[ScoredProfile]:
        """Score, bucket, and add reasoning to all profiles."""
        if not profiles:
            return []

        weights = search_spec.weights.normalized()
        thresholds = recruiter_config.thresholds

        # Step 1: Score all profiles
        pre_scored: list[dict] = []
        for profile in profiles:
            scored = self._score_profile(profile, search_spec, weights)
            pre_scored.append(scored)

        # Step 2: Assign buckets
        for item in pre_scored:
            item["bucket"] = self._assign_bucket(item, thresholds, search_spec)

        # Step 3: Generate reasoning (batch LLM call for accepted + close)
        reasoning_map = self._generate_reasoning(pre_scored, search_spec)

        # Step 4: Build ScoredProfile objects
        scored_profiles: list[ScoredProfile] = []
        for item in pre_scored:
            profile = item["profile"]
            bucket = item["bucket"]
            url = profile.primary_url

            if bucket == "rejected":
                reasoning = "Does not meet must-have requirements."
            else:
                reasoning = reasoning_map.get(url, self._fallback_reasoning(item))

            scored_profiles.append(
                ScoredProfile(
                    profile=profile,
                    score_pct=round(item["score"], 4),
                    matched=item["matched"],
                    gaps=item["gaps"],
                    reasoning=reasoning,
                    bucket=bucket,
                )
            )

        # Sort: accepted first (by score desc), then close (by score desc), then rejected
        order = {"accepted": 0, "close": 1, "rejected": 2}
        scored_profiles.sort(key=lambda x: (order[x.bucket], -x.score_pct))
        return scored_profiles

    def _score_profile(
        self, profile: DedupedProfile, spec: SearchSpec, weights: WeightConfig
    ) -> dict:
        """Compute weighted score for a single profile."""
        profile_skills_lower = {s.lower() for s in profile.skills}

        # Must-have matching
        must_haves = [s.lower() for s in spec.must_have_skills]
        matched_must = [s for s in must_haves if s in profile_skills_lower]
        must_have_pct = len(matched_must) / len(must_haves) if must_haves else 1.0

        # Nice-to-have matching
        nice_haves = [s.lower() for s in spec.nice_to_have_skills]
        matched_nice = [s for s in nice_haves if s in profile_skills_lower]
        nice_have_pct = len(matched_nice) / len(nice_haves) if nice_haves else 0.0

        # Seniority proximity
        seniority_score = self._seniority_proximity(profile, spec)

        # Location match
        location_score = self._location_match(profile, spec)

        # Weighted total
        score = (
            weights.must_have_skills * must_have_pct
            + weights.nice_to_have_skills * nice_have_pct
            + weights.seniority_level * seniority_score
            + weights.location_match * location_score
        )

        # Build matched/gaps lists (original case)
        matched: list[str] = []
        gaps: list[str] = []
        for s in spec.must_have_skills:
            if s.lower() in profile_skills_lower:
                matched.append(s)
            else:
                gaps.append(s)
        for s in spec.nice_to_have_skills:
            if s.lower() in profile_skills_lower:
                matched.append(s)

        return {
            "profile": profile,
            "score": min(score, 1.0),
            "matched": matched,
            "gaps": gaps,
            "must_have_pct": must_have_pct,
            "matched_must_count": len(matched_must),
            "total_must_count": len(must_haves),
        }

    def _seniority_proximity(self, profile: DedupedProfile, spec: SearchSpec) -> float:
        """Compare profile seniority to required. Exact=1.0, one-level=0.5, >1=0.0."""
        profile_title_lower = profile.title.lower()
        required = spec.seniority_level.lower()

        # Detect seniority from profile title
        detected = "mid"
        for level in SENIORITY_LEVELS:
            if level in profile_title_lower:
                detected = level
                break
        # Additional title pattern detection
        if "principal" in profile_title_lower or "staff" in profile_title_lower:
            detected = "principal"
        elif "lead" in profile_title_lower or "architect" in profile_title_lower:
            detected = "lead"
        elif "senior" in profile_title_lower or "sr." in profile_title_lower:
            detected = "senior"
        elif "junior" in profile_title_lower or "jr." in profile_title_lower or "associate" in profile_title_lower:
            detected = "junior"

        req_idx = SENIORITY_LEVELS.index(required) if required in SENIORITY_LEVELS else 2
        det_idx = SENIORITY_LEVELS.index(detected) if detected in SENIORITY_LEVELS else 2

        diff = abs(req_idx - det_idx)
        if diff == 0:
            return 1.0
        elif diff == 1:
            return 0.5
        else:
            return 0.0

    def _location_match(self, profile: DedupedProfile, spec: SearchSpec) -> float:
        """1.0 if profile location contains spec location, 0.5 if unknown, 0.0 otherwise."""
        if not profile.location:
            return 0.5
        if not spec.location:
            return 0.5
        spec_loc = spec.location.lower()
        prof_loc = profile.location.lower()
        # Check if any token in spec_location appears in profile location
        spec_tokens = spec_loc.replace(",", " ").split()
        for token in spec_tokens:
            if len(token) > 2 and token in prof_loc:
                return 1.0
        if "remote" in spec_loc or "remote" in prof_loc:
            return 0.8
        return 0.0

    def _assign_bucket(self, item: dict, thresholds, spec: SearchSpec) -> str:
        """Assign accepted/close/rejected bucket based on score + must-have gate."""
        score = item["score"]
        must_have_pct = item["must_have_pct"]
        total_must = item["total_must_count"]
        matched_must = item["matched_must_count"]
        missing_must = total_must - matched_must

        # Must-have gate: missing more than 1 must-have → hard rejected
        if missing_must > 1:
            return "rejected"

        # All must-haves present and score meets threshold → accepted
        if must_have_pct >= 1.0 and score >= thresholds.accepted_min_score:
            return "accepted"

        # Close: high score OR missing exactly 1 must-have
        if score >= thresholds.close_min_score or missing_must == 1:
            return "close"

        return "rejected"

    def _generate_reasoning(
        self, pre_scored: list[dict], spec: SearchSpec
    ) -> dict[str, str]:
        """Generate NL reasoning for accepted + close profiles.

        Batches all into a single LLM call (or returns mocks if api_key is mock-key).
        Returns dict mapping profile_url -> reasoning string.
        """
        candidates = [
            item for item in pre_scored if item["bucket"] in ("accepted", "close")
        ]
        if not candidates:
            return {}

        if self._is_mock_mode():
            return self._mock_reasoning(candidates, spec)

        # Build batch prompt
        profiles_data = []
        for item in candidates:
            p = item["profile"]
            profiles_data.append(
                {
                    "profile_url": p.primary_url,
                    "name": p.name,
                    "title": p.title,
                    "company": p.company,
                    "matched_skills": item["matched"],
                    "gaps": item["gaps"],
                    "score_pct": round(item["score"] * 100, 1),
                }
            )

        import json

        user_prompt = f"""Job spec: {spec.role_title} ({spec.seniority_level}) — Must-haves: {', '.join(spec.must_have_skills)}

Candidates (JSON array):
{json.dumps(profiles_data, indent=2)}

Return a JSON array where each object has exactly "profile_url" and "reasoning" fields.
Reasoning should be 1-2 sentences specific to this candidate's fit."""

        try:
            response_text = self._call_llm(SYSTEM_PROMPT_REASONING, user_prompt)
            data = self._parse_json_response(response_text)
            return {item["profile_url"]: item["reasoning"] for item in data}
        except Exception as e:
            logger.error(f"Reasoning generation failed: {e}. Using fallback reasoning.")
            return {item["profile"]["primary_url"]: self._fallback_reasoning(item) for item in candidates}

    def _mock_reasoning(self, candidates: list[dict], spec: SearchSpec) -> dict[str, str]:
        """Generate mock reasoning strings based on score and match data."""
        result = {}
        for item in candidates:
            p = item["profile"]
            score = item["score"]
            matched = item["matched"]
            gaps = item["gaps"]
            bucket = item["bucket"]

            if bucket == "accepted":
                if score >= 0.90:
                    reasoning = (
                        f"{p.name} is an excellent match for the {spec.role_title} role, "
                        f"demonstrating strong proficiency in {', '.join(matched[:3])}. "
                        f"Their experience at {p.company} aligns closely with the seniority and technical requirements."
                    )
                else:
                    gap_note = f" Minor gaps in {', '.join(gaps[:2])} are manageable." if gaps else ""
                    reasoning = (
                        f"{p.name} meets all core requirements with solid skills in {', '.join(matched[:3])}.{gap_note} "
                        f"Overall a strong candidate worth pursuing."
                    )
            else:  # close
                if gaps:
                    reasoning = (
                        f"{p.name} shows solid potential with experience in {', '.join(matched[:3])}, "
                        f"but is missing {', '.join(gaps[:2])} which are listed as must-haves. "
                        f"Consider if these gaps can be addressed through onboarding."
                    )
                else:
                    reasoning = (
                        f"{p.name} has relevant skills in {', '.join(matched[:2])} but the overall profile "
                        f"score ({round(score*100)}%) falls slightly below the acceptance threshold. "
                        f"May be worth a screening call."
                    )

            result[p.primary_url] = reasoning
        return result

    def _fallback_reasoning(self, item: dict) -> str:
        """Simple fallback reasoning without LLM."""
        p = item["profile"]
        score = item["score"]
        matched = item["matched"]
        gaps = item["gaps"]
        gap_str = f" Missing: {', '.join(gaps[:2])}." if gaps else ""
        return (
            f"{p.name} scored {round(score * 100)}% — matched skills: {', '.join(matched[:3])}.{gap_str}"
        )

import logging
from schemas.models import RawProfile, DedupedProfile, AltProfile, Experience, Education
from config import APP_CONFIG

logger = logging.getLogger(__name__)

# Priority order for source data quality (higher index = higher priority when merging)
SOURCE_PRIORITY = {"google_cse": 1, "naukri": 2, "pdl": 3, "resume_upload": 4}

_NAME_OVERLAP_THRESHOLD = APP_CONFIG.pipeline.name_overlap_threshold


def _normalize_name(name: str) -> str:
    """Lowercase, strip, remove common suffixes for comparison."""
    return name.lower().strip()


def _name_tokens_overlap(name_a: str, name_b: str, threshold: float = _NAME_OVERLAP_THRESHOLD) -> bool:
    """Return True if >threshold fraction of tokens overlap between two names."""
    tokens_a = set(_normalize_name(name_a).split())
    tokens_b = set(_normalize_name(name_b).split())
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b)
    smaller = min(len(tokens_a), len(tokens_b))
    return (overlap / smaller) >= threshold


def _profiles_are_duplicate(a: RawProfile, b: RawProfile) -> bool:
    """Identity resolution using P1–P4 priority signals."""
    # P1: exact email match
    if a.email and b.email and a.email.lower().strip() == b.email.lower().strip():
        return True

    # P4: phone match (check early since it's unambiguous)
    if a.phone and b.phone and a.phone.strip() == b.phone.strip():
        return True

    # P2: fuzzy name match + same company
    if _name_tokens_overlap(a.name, b.name):
        company_a = a.company.lower().strip()
        company_b = b.company.lower().strip()
        if company_a and company_b and company_a == company_b:
            return True
        # Also check experience overlap
        if _has_experience_overlap(a, b):
            return True

    # P3: normalized title + location match (same name required for P3)
    if _name_tokens_overlap(a.name, b.name):
        title_a = a.title.lower().strip()
        title_b = b.title.lower().strip()
        loc_a = (a.location or "").lower().strip()
        loc_b = (b.location or "").lower().strip()
        if title_a and title_b and title_a == title_b:
            if loc_a and loc_b and loc_a == loc_b:
                return True

    return False


def _has_experience_overlap(a: RawProfile, b: RawProfile) -> bool:
    """Check if two profiles share at least one overlapping job (company + title)."""
    companies_a = {exp.company.lower().strip() for exp in a.experience}
    companies_b = {exp.company.lower().strip() for exp in b.experience}
    return bool(companies_a & companies_b)


def _source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(source, 0)


def _merge_profiles(primary: RawProfile, duplicate: RawProfile) -> tuple[RawProfile, dict[str, str]]:
    """Merge duplicate into primary. Higher-priority source wins per field.
    Returns (merged_profile, merged_fields_map).
    """
    merged_fields: dict[str, str] = {}

    if _source_priority(duplicate.source) > _source_priority(primary.source):
        # Duplicate has higher priority — it wins on non-null fields
        winner = duplicate
        loser = primary
    else:
        winner = primary
        loser = duplicate

    def pick(field: str):
        w_val = getattr(winner, field)
        l_val = getattr(loser, field)
        if w_val:
            merged_fields[field] = winner.source
            return w_val
        elif l_val:
            merged_fields[field] = loser.source
            return l_val
        return None

    # Union skills
    all_skills = list(dict.fromkeys(primary.skills + duplicate.skills))

    # Merge experience: take from highest priority source
    experience = winner.experience if winner.experience else loser.experience
    education = winner.education if winner.education else loser.education

    merged = RawProfile(
        name=pick("name") or primary.name,
        title=pick("title") or primary.title,
        company=pick("company") or primary.company,
        skills=all_skills,
        experience=experience,
        education=education,
        source=winner.source,
        profile_url=winner.profile_url,
        email=pick("email"),
        phone=pick("phone"),
        location=pick("location"),
    )
    return merged, merged_fields


class DeduplicationStage:
    """Stage 04: Deduplication & Merging.

    Pure Python, no LLM needed. Uses P1–P4 identity signals to find duplicates,
    merges them with most-recent / highest-priority source winning per field.
    """

    def deduplicate(self, raw_profiles: list[RawProfile]) -> list[DedupedProfile]:
        """Deduplicate raw profiles and return merged DedupedProfile list."""
        if not raw_profiles:
            return []

        # Group profiles into clusters of duplicates
        clusters: list[list[RawProfile]] = []
        assigned: list[bool] = [False] * len(raw_profiles)

        for i, profile_a in enumerate(raw_profiles):
            if assigned[i]:
                continue
            cluster = [profile_a]
            assigned[i] = True
            for j, profile_b in enumerate(raw_profiles):
                if i == j or assigned[j]:
                    continue
                if _profiles_are_duplicate(profile_a, profile_b):
                    cluster.append(profile_b)
                    assigned[j] = True
            clusters.append(cluster)

        logger.info(
            f"Dedup: {len(raw_profiles)} raw → {len(clusters)} unique profiles "
            f"({len(raw_profiles) - len(clusters)} duplicates removed)"
        )

        deduped: list[DedupedProfile] = []
        for cluster in clusters:
            deduped.append(self._build_deduped_profile(cluster))

        return deduped

    def _build_deduped_profile(self, cluster: list[RawProfile]) -> DedupedProfile:
        """Merge a cluster of duplicate profiles into a single DedupedProfile."""
        if len(cluster) == 1:
            p = cluster[0]
            return DedupedProfile(
                name=p.name,
                title=p.title,
                company=p.company,
                skills=p.skills,
                experience=p.experience,
                education=p.education,
                location=p.location,
                email=p.email,
                phone=p.phone,
                primary_source=p.source,
                primary_url=p.profile_url,
                alt_profiles=[],
                merged_fields={},
            )

        # Sort cluster by source priority (highest first)
        sorted_cluster = sorted(cluster, key=lambda p: _source_priority(p.source), reverse=True)
        primary = sorted_cluster[0]
        duplicates = sorted_cluster[1:]

        # Iteratively merge all duplicates into primary
        current_merged, merged_fields = primary, {}
        for dup in duplicates:
            current_merged, extra_fields = _merge_profiles(
                RawProfile(
                    name=current_merged.name,
                    title=current_merged.title,
                    company=current_merged.company,
                    skills=current_merged.skills,
                    experience=current_merged.experience,
                    education=current_merged.education,
                    source=current_merged.source,
                    profile_url=current_merged.profile_url,
                    email=current_merged.email,
                    phone=current_merged.phone,
                    location=current_merged.location,
                ),
                dup,
            )
            merged_fields.update(extra_fields)

        # Build alt_profiles list
        alt_profiles: list[AltProfile] = []
        for dup in duplicates:
            field_diffs: dict[str, str] = {}
            for field in ["title", "company", "location", "email"]:
                primary_val = getattr(primary, field)
                dup_val = getattr(dup, field)
                if primary_val != dup_val and dup_val:
                    field_diffs[field] = str(dup_val)
            alt_profiles.append(
                AltProfile(source=dup.source, url=dup.profile_url, field_diffs=field_diffs)
            )

        return DedupedProfile(
            name=current_merged.name,
            title=current_merged.title,
            company=current_merged.company,
            skills=current_merged.skills,
            experience=current_merged.experience,
            education=current_merged.education,
            location=current_merged.location,
            email=current_merged.email,
            phone=current_merged.phone,
            primary_source=current_merged.source,
            primary_url=current_merged.profile_url,
            alt_profiles=alt_profiles,
            merged_fields=merged_fields,
        )

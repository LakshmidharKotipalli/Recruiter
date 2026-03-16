"""Anthropic tool_use JSON schemas for the OrchestratorAgent."""

# ── Individual tool definitions ──────────────────────────────────────────────

EXTRACT_JD_TOOL = {
    "name": "extract_jd",
    "description": (
        "Run the JD Extraction Agent (3-step: clean → enrich → extract). "
        "Returns a SearchSpec with role_title, must_have_skills, nice_to_have_skills, "
        "seniority_level, location, jd_quality_score, inferred_skills, and core_requirements."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "raw_jd": {
                "type": "string",
                "description": "The raw job description text as provided by the recruiter.",
            },
            "recruiter_config_json": {
                "type": "string",
                "description": "JSON-serialized RecruiterConfig (weights, thresholds).",
            },
        },
        "required": ["raw_jd", "recruiter_config_json"],
    },
}

GENERATE_QUERIES_TOOL = {
    "name": "generate_queries",
    "description": (
        "Run the Query Generation Agent. Produces Boolean search queries for each source "
        "(google_cse, naukri, pdl) with primary and fallback variants. "
        "Use strategy='broader' to expand skills/seniority, "
        "'location_relaxed' to drop location constraints."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "search_spec_json": {
                "type": "string",
                "description": "JSON-serialized SearchSpec from extract_jd.",
            },
            "strategy": {
                "type": "string",
                "enum": ["precise", "broader", "location_relaxed"],
                "description": (
                    "Search strategy. 'precise' = strict match (default). "
                    "'broader' = expand skill synonyms, relax seniority. "
                    "'location_relaxed' = drop location filter entirely."
                ),
            },
        },
        "required": ["search_spec_json"],
    },
}

FETCH_PROFILES_TOOL = {
    "name": "fetch_profiles",
    "description": (
        "Run the Profile Fetching Agent — dispatches queries to all sources in parallel "
        "and returns raw profiles. Set use_fallback_queries=true to also run the "
        "fallback query variants when the primary set returns thin results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query_set_json": {
                "type": "string",
                "description": "JSON-serialized QuerySet from generate_queries.",
            },
            "use_fallback_queries": {
                "type": "boolean",
                "description": "If true, also execute fallback queries for each source.",
            },
        },
        "required": ["query_set_json"],
    },
}

SCORE_CANDIDATES_TOOL = {
    "name": "score_candidates",
    "description": (
        "Run Deduplication + Ranking. Deduplicates raw profiles across sources, "
        "scores each against the SearchSpec using the recruiter's weight config, "
        "and buckets them into accepted / close / rejected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "search_spec_json": {
                "type": "string",
                "description": "JSON-serialized SearchSpec (may be updated by HITL).",
            },
            "recruiter_config_json": {
                "type": "string",
                "description": "JSON-serialized RecruiterConfig.",
            },
        },
        "required": ["search_spec_json", "recruiter_config_json"],
    },
}

ASSESS_QUALITY_TOOL = {
    "name": "assess_quality",
    "description": (
        "Evaluate the quality of the current shortlist. Returns a recommended action: "
        "'accept' (shortlist is good), 'refine_broader' (too few profiles — broaden search), "
        "'refine_location_relaxed' (many close but failing location — relax it), "
        "or 'force_accept' (max iterations reached with no accepted candidates)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "accepted_count": {
                "type": "integer",
                "description": "Number of candidates in the accepted bucket.",
            },
            "close_count": {
                "type": "integer",
                "description": "Number of candidates in the close bucket.",
            },
            "total_raw": {
                "type": "integer",
                "description": "Total raw profiles collected across all sources.",
            },
            "iteration": {
                "type": "integer",
                "description": "Current iteration number (1-based).",
            },
            "jd_quality_score": {
                "type": "integer",
                "description": "JD quality score 0–10 from extract_jd.",
            },
        },
        "required": [
            "accepted_count",
            "close_count",
            "total_raw",
            "iteration",
            "jd_quality_score",
        ],
    },
}

PARSE_RESUMES_TOOL = {
    "name": "parse_resumes",
    "description": (
        "Parse uploaded resume files into structured candidate profiles. "
        "Each resume is extracted into a RawProfile with name, title, skills, "
        "experience, and education."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "resume_count": {
                "type": "integer",
                "description": "Number of resumes to parse.",
            },
        },
        "required": ["resume_count"],
    },
}

RELAX_REQUIREMENTS_TOOL = {
    "name": "relax_requirements",
    "description": (
        "Relax search requirements to accept more candidates from uploaded resumes. "
        "Use when assess_quality recommends refinement but no additional profiles can "
        "be fetched. 'broader' moves the least-critical must-have skill to nice-to-have. "
        "'location_relaxed' clears the location constraint."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "enum": ["broader", "location_relaxed"],
                "description": "Relaxation strategy.",
            },
        },
        "required": ["strategy"],
    },
}

# ── Composed tool sets ───────────────────────────────────────────────────────

PIPELINE_TOOLS = [
    EXTRACT_JD_TOOL,
    GENERATE_QUERIES_TOOL,
    FETCH_PROFILES_TOOL,
    SCORE_CANDIDATES_TOOL,
    ASSESS_QUALITY_TOOL,
]

RESUME_PIPELINE_TOOLS = [
    EXTRACT_JD_TOOL,
    PARSE_RESUMES_TOOL,
    SCORE_CANDIDATES_TOOL,
    RELAX_REQUIREMENTS_TOOL,
    ASSESS_QUALITY_TOOL,
]

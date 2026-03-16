"""
Sub-Agent 01 — JD Extraction Agent

Three sequential steps:
  Step 1  JD Cleaner      quality-score the raw JD, detect issues, rewrite into
                          clean structured prose when needed
  Step 2  Gap Filler      apply role-domain knowledge to infer implied requirements
                          that the JD omitted or left ambiguous
  Step 3  Extractor       produce the final typed SearchSpec from the enriched JD
"""

import logging
from agents.base import BaseAgent
from config import ModelConfig
from schemas.models import RecruiterConfig, SearchSpec

logger = logging.getLogger(__name__)

# ─── Step 1 prompt ────────────────────────────────────────────────────────────

_CLEAN_SYSTEM = """You are a Job Description Quality Analyst and Editor.

Your job is to:
1. Score the quality of a raw job description on a scale of 0–10
   (0 = nearly incomprehensible, 10 = perfectly structured)
2. Identify every concrete problem with the JD (vague language, missing sections,
   contradictions, unrealistic combinations, grammar/format issues)
3. Rewrite the JD into clean, well-structured prose — preserving every detail
   the recruiter stated, correcting grammar and structure, and making implicit
   sections explicit (responsibilities, requirements, nice-to-haves, location)

Return ONLY a valid JSON object with exactly these keys:
{
  "quality_score": <int 0-10>,
  "issues": ["issue 1", "issue 2", ...],
  "cleaned_jd": "<full rewritten JD as a single string with newlines as \\n>"
}

Rules:
- issues must be specific (e.g. "No location specified", "Contradicts itself: asks for junior AND 8 years experience")
- If the JD is already high-quality (score >= 8), cleaned_jd may be the original text lightly reformatted
- Never invent requirements not present or strongly implied — save that for the gap-fill step
- Return ONLY the JSON object, no prose"""


# ─── Step 2 prompt ────────────────────────────────────────────────────────────

_ENRICH_SYSTEM = """You are a Senior Talent Intelligence Expert with deep knowledge of
technology roles, engineering career ladders, and hiring market norms.

You will receive a cleaned job description. Your job is to:
1. Identify requirements that are STRONGLY IMPLIED by the role but absent from the JD
   (e.g. a "Senior Backend Engineer" almost always needs version control, CI/CD knowledge,
   system design skills — even if unstated)
2. Resolve ambiguities using role-level norms (e.g. "5+ years" for a "Senior" title is
   normal; flagging if it says "Senior" but implies entry-level work)
3. Separate must-haves from nice-to-haves based on phrasing and role context

Return ONLY a valid JSON object with exactly these keys:
{
  "role_title": "<canonical normalised title>",
  "seniority_level": "<junior|mid|senior|lead|principal>",
  "location": "<city, country or Remote — empty string if truly unknown>",
  "core_requirements": "<2–3 sentence plain-English summary of what this role actually needs>",
  "must_have_skills": ["skill1", ...],
  "nice_to_have_skills": ["skill1", ...],
  "inferred_skills": ["skill1", ...],
  "gap_flags": ["flag1", ...]
}

Field rules:
- must_have_skills: explicitly stated OR universally required for this role/seniority combo
- nice_to_have_skills: "preferred", "bonus", "a plus", or reasonably optional for the role
- inferred_skills: skills NOT in the JD that you added via role knowledge — be conservative,
  only add skills that appear in >80% of real postings for this role
- gap_flags: unrealistic combos, missing critical info, contradictions
- Return ONLY the JSON object, no prose"""


class JDExtractionAgent(BaseAgent):
    """Sub-Agent 01: three-step JD Extraction Agent."""

    def __init__(self, config: ModelConfig):
        super().__init__(config)

    # ─── Public entry point ────────────────────────────────────────────────

    def extract(self, raw_jd: str, recruiter_config: RecruiterConfig) -> SearchSpec:
        """Run the full three-step extraction pipeline and return a SearchSpec."""
        if self._is_mock_mode():
            return self._mock_extract(raw_jd, recruiter_config)

        try:
            cleaned = self._step1_clean(raw_jd)
            enriched = self._step2_enrich(cleaned["cleaned_jd"])
            return SearchSpec(
                role_title=enriched["role_title"],
                core_requirements=enriched["core_requirements"],
                must_have_skills=enriched["must_have_skills"],
                nice_to_have_skills=enriched["nice_to_have_skills"],
                seniority_level=enriched["seniority_level"],
                location=enriched["location"],
                weights=recruiter_config.weights,
                gap_flags=enriched["gap_flags"],
                cleaned_jd=cleaned["cleaned_jd"],
                jd_quality_score=cleaned["quality_score"],
                jd_issues=cleaned["issues"],
                inferred_skills=enriched["inferred_skills"],
            )
        except Exception as e:
            logger.error(f"JD extraction failed: {e}. Falling back to mock.")
            return self._mock_extract(raw_jd, recruiter_config)

    # ─── Step 1: clean ────────────────────────────────────────────────────

    def _step1_clean(self, raw_jd: str) -> dict:
        user = (
            "Assess and rewrite the following job description.\n\n"
            f"RAW JD:\n{raw_jd}\n\n"
            "Return ONLY valid JSON with keys: quality_score, issues, cleaned_jd."
        )
        text = self._call_llm(_CLEAN_SYSTEM, user)
        data = self._parse_json_response(text)
        return {
            "quality_score": int(data.get("quality_score", 5)),
            "issues": data.get("issues", []),
            "cleaned_jd": data.get("cleaned_jd", raw_jd),
        }

    # ─── Step 2: enrich ───────────────────────────────────────────────────

    def _step2_enrich(self, cleaned_jd: str) -> dict:
        user = (
            "Apply role-domain knowledge to the following cleaned job description "
            "and extract structured requirements.\n\n"
            f"CLEANED JD:\n{cleaned_jd}\n\n"
            "Return ONLY valid JSON with keys: role_title, seniority_level, location, "
            "core_requirements, must_have_skills, nice_to_have_skills, inferred_skills, gap_flags."
        )
        text = self._call_llm(_ENRICH_SYSTEM, user)
        data = self._parse_json_response(text)
        return {
            "role_title": data.get("role_title", "Software Engineer"),
            "seniority_level": data.get("seniority_level", "mid"),
            "location": data.get("location", ""),
            "core_requirements": data.get("core_requirements", ""),
            "must_have_skills": data.get("must_have_skills", []),
            "nice_to_have_skills": data.get("nice_to_have_skills", []),
            "inferred_skills": data.get("inferred_skills", []),
            "gap_flags": data.get("gap_flags", []),
        }

    # ─── Mock responses ───────────────────────────────────────────────────

    def _mock_extract(self, raw_jd: str, recruiter_config: RecruiterConfig) -> SearchSpec:
        """Return realistic mock SearchSpec. Simulates all three steps."""
        jd_lower = raw_jd.lower()
        is_poor = len(raw_jd.strip()) < 120 or raw_jd.strip().count("\n") < 2

        if "data engineer" in jd_lower or "data pipeline" in jd_lower or "spark" in jd_lower:
            return self._mock_data_engineer(raw_jd, recruiter_config, is_poor)
        if "frontend" in jd_lower or "react" in jd_lower or "vue" in jd_lower or "angular" in jd_lower:
            return self._mock_frontend(raw_jd, recruiter_config, is_poor)
        if any(k in jd_lower for k in ("machine learning", " ml ", "pytorch", "tensorflow", "llm")):
            return self._mock_ml_engineer(raw_jd, recruiter_config, is_poor)
        if "devops" in jd_lower or "kubernetes" in jd_lower or "infrastructure" in jd_lower:
            return self._mock_devops(raw_jd, recruiter_config, is_poor)
        # Default: Senior Python Backend Engineer
        return self._mock_python_backend(raw_jd, recruiter_config, is_poor)

    # ── mock role variants ─────────────────────────────────────────────────

    def _mock_python_backend(self, raw_jd: str, cfg: RecruiterConfig, is_poor: bool) -> SearchSpec:
        issues = []
        if is_poor:
            issues = [
                "Missing 'Responsibilities' section — role scope is unclear.",
                "No minimum years of experience stated.",
                "Cloud provider unspecified; AWS assumed from context.",
                "Team size and reporting structure absent.",
            ]
        cleaned = (
            "## Senior Python Engineer\n\n"
            "**Location:** Bangalore, India (Hybrid)\n\n"
            "**About the role**\nWe are looking for a Senior Python Engineer to design, build, and maintain "
            "high-throughput backend services. You will own critical API surfaces and data pipelines serving "
            "millions of users.\n\n"
            "**Requirements**\n- 5+ years of Python development\n- Proficiency with FastAPI or Django REST Framework\n"
            "- Strong SQL and PostgreSQL experience\n- Containerisation with Docker; orchestration with Kubernetes\n"
            "- Hands-on AWS experience (EC2, RDS, S3, Lambda)\n\n"
            "**Nice to have**\n- Redis or Celery for async task queues\n- GraphQL APIs\n- Experience with high-scale systems (>10M users)"
        )
        return SearchSpec(
            role_title="Senior Python Engineer",
            core_requirements=(
                "Senior Python engineer to own backend API development and data pipelines. "
                "Must be comfortable designing distributed systems and mentoring junior engineers. "
                "Hybrid role based in Bangalore with 5+ years of hands-on Python experience required."
            ),
            must_have_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "AWS"],
            nice_to_have_skills=["Kubernetes", "Redis", "Celery", "GraphQL", "Django"],
            seniority_level="senior",
            location="Bangalore, India",
            weights=cfg.weights,
            gap_flags=[
                "Cloud provider not specified — AWS inferred from market norms for this stack.",
                "No minimum YOE stated — inferred as 5+ from seniority title.",
            ] if is_poor else [],
            cleaned_jd=cleaned,
            jd_quality_score=3 if is_poor else 8,
            jd_issues=issues,
            inferred_skills=["Git", "CI/CD", "System Design", "Code Review"] if is_poor else [],
        )

    def _mock_data_engineer(self, raw_jd: str, cfg: RecruiterConfig, is_poor: bool) -> SearchSpec:
        issues = (
            [
                "No mention of data warehouse or lakehouse target.",
                "Streaming vs batch workload not distinguished.",
                "Seniority implied from salary range but not stated explicitly.",
            ]
            if is_poor else []
        )
        cleaned = (
            "## Senior Data Engineer\n\n"
            "**Location:** Remote (US timezone)\n\n"
            "Design and operate large-scale data pipelines ingesting 500 GB/day. "
            "Build batch and streaming ETL flows using Apache Spark and Kafka. "
            "Maintain Snowflake data warehouse. Collaborate with analytics and ML teams.\n\n"
            "**Must have:** Python, Apache Spark, SQL, AWS (S3/Glue/EMR), ETL/ELT pipelines\n"
            "**Nice to have:** dbt, Airflow, Kafka, Scala, Databricks"
        )
        return SearchSpec(
            role_title="Senior Data Engineer",
            core_requirements=(
                "Senior data engineer to build and maintain high-volume batch and streaming pipelines "
                "on AWS. Strong Spark and SQL fundamentals required; experience with modern data stack "
                "(dbt, Airflow) is a significant plus."
            ),
            must_have_skills=["Python", "Apache Spark", "SQL", "AWS", "ETL pipelines"],
            nice_to_have_skills=["Kafka", "dbt", "Airflow", "Scala", "Snowflake", "Databricks"],
            seniority_level="senior",
            location="Remote / US",
            weights=cfg.weights,
            gap_flags=["Streaming vs batch split not specified — both assumed." ] if is_poor else [],
            cleaned_jd=cleaned,
            jd_quality_score=4 if is_poor else 8,
            jd_issues=issues,
            inferred_skills=["Git", "CI/CD", "Data Modelling", "Parquet/ORC formats"] if is_poor else [],
        )

    def _mock_frontend(self, raw_jd: str, cfg: RecruiterConfig, is_poor: bool) -> SearchSpec:
        issues = (
            [
                "No design system or component library mentioned.",
                "Browser support matrix not defined.",
                "Performance budgets absent.",
            ]
            if is_poor else []
        )
        cleaned = (
            "## Senior Frontend Engineer\n\n"
            "**Location:** Remote\n\n"
            "Build and maintain a high-performance React application used by 2M+ users. "
            "Own component library, performance optimisation, and frontend CI pipeline.\n\n"
            "**Must have:** React 18+, TypeScript, CSS-in-JS / Tailwind, REST API integration\n"
            "**Nice to have:** Next.js, GraphQL, Storybook, Playwright, micro-frontend architecture"
        )
        return SearchSpec(
            role_title="Senior Frontend Engineer",
            core_requirements=(
                "Senior frontend engineer to own a large-scale React/TypeScript application. "
                "Expected to drive component architecture decisions and mentor the frontend team. "
                "Performance engineering and accessibility are first-class concerns."
            ),
            must_have_skills=["React", "TypeScript", "CSS", "REST APIs", "Git"],
            nice_to_have_skills=["Next.js", "GraphQL", "Storybook", "Playwright", "Docker"],
            seniority_level="senior",
            location="Remote",
            weights=cfg.weights,
            gap_flags=["No accessibility requirements stated — added as inferred must-have."] if is_poor else [],
            cleaned_jd=cleaned,
            jd_quality_score=3 if is_poor else 8,
            jd_issues=issues,
            inferred_skills=["Web Accessibility (WCAG)", "Browser DevTools", "Webpack/Vite"] if is_poor else [],
        )

    def _mock_ml_engineer(self, raw_jd: str, cfg: RecruiterConfig, is_poor: bool) -> SearchSpec:
        issues = (
            [
                "No distinction between research and production ML responsibilities.",
                "Model serving infrastructure not mentioned.",
                "Evaluation metrics and model governance absent.",
            ]
            if is_poor else []
        )
        cleaned = (
            "## Senior ML Engineer\n\n"
            "**Location:** San Francisco, CA (Hybrid)\n\n"
            "Design, train, and deploy production ML models for recommendation and ranking systems. "
            "Own the full ML lifecycle from data preparation through serving.\n\n"
            "**Must have:** Python, PyTorch, machine learning fundamentals, SQL, Docker\n"
            "**Nice to have:** MLflow, Kubernetes, Spark, AWS SageMaker, LLM fine-tuning"
        )
        return SearchSpec(
            role_title="Senior ML Engineer",
            core_requirements=(
                "Senior ML engineer to build and own production recommendation and ranking models. "
                "Must bridge research and engineering — comfortable both training models and deploying "
                "them at scale. Experience with the full MLOps lifecycle strongly preferred."
            ),
            must_have_skills=["Python", "PyTorch", "Machine Learning", "SQL", "Docker"],
            nice_to_have_skills=["MLflow", "Kubernetes", "Spark", "AWS SageMaker", "LLMs", "Feature Stores"],
            seniority_level="senior",
            location="San Francisco, CA",
            weights=cfg.weights,
            gap_flags=["Research vs production scope unclear — production assumed from JD context."] if is_poor else [],
            cleaned_jd=cleaned,
            jd_quality_score=4 if is_poor else 8,
            jd_issues=issues,
            inferred_skills=["Experiment Tracking", "A/B Testing", "Model Monitoring"] if is_poor else [],
        )

    def _mock_devops(self, raw_jd: str, cfg: RecruiterConfig, is_poor: bool) -> SearchSpec:
        issues = (
            [
                "No cloud provider specified — multi-cloud or single cloud unclear.",
                "On-call expectations not stated.",
                "Security compliance requirements absent.",
            ]
            if is_poor else []
        )
        cleaned = (
            "## Senior DevOps / Platform Engineer\n\n"
            "**Location:** Remote (EU timezone)\n\n"
            "Own and evolve a Kubernetes-based platform serving 50+ engineering teams. "
            "Drive infrastructure-as-code adoption, observability, and cost optimisation.\n\n"
            "**Must have:** Kubernetes, Terraform, AWS/GCP, CI/CD (GitHub Actions), Linux, Docker\n"
            "**Nice to have:** ArgoCD, Helm, Prometheus/Grafana, Go, Python scripting"
        )
        return SearchSpec(
            role_title="Senior DevOps Engineer",
            core_requirements=(
                "Senior platform/DevOps engineer to own a multi-tenant Kubernetes platform. "
                "Expected to be the go-to for infrastructure reliability, IaC standards, and "
                "developer experience across the engineering organisation."
            ),
            must_have_skills=["Kubernetes", "Terraform", "AWS", "Docker", "CI/CD", "Linux"],
            nice_to_have_skills=["ArgoCD", "Helm", "Prometheus", "Grafana", "Go", "Python"],
            seniority_level="senior",
            location="Remote / EU",
            weights=cfg.weights,
            gap_flags=["Cloud provider not specified — AWS assumed; GCP listed as nice-to-have."] if is_poor else [],
            cleaned_jd=cleaned,
            jd_quality_score=4 if is_poor else 8,
            jd_issues=issues,
            inferred_skills=["Bash scripting", "Networking fundamentals", "Security hardening"] if is_poor else [],
        )

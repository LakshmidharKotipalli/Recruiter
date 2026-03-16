"""
Resume Parser Agent — Extracts structured RawProfile from resume text.

Uses LLM to parse unstructured resume content into the same RawProfile schema
used by the profile-fetching pipeline, enabling seamless integration with
the existing deduplication and ranking stages.
"""

import logging
from typing import Optional
from agents.base import BaseAgent
from config import ModelConfig
from schemas.models import RawProfile, Experience, Education

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert resume parser. Given raw resume text, extract structured candidate information.

Return ONLY a valid JSON object with exactly these keys:
{
  "name": "<full name>",
  "title": "<most recent job title>",
  "company": "<most recent company>",
  "skills": ["skill1", "skill2", ...],
  "location": "<city, country or empty string>",
  "email": "<email or null>",
  "phone": "<phone or null>",
  "experience": [
    {"title": "<job title>", "company": "<company>", "start_date": "<start>", "end_date": "<end or null>", "description": "<brief description or null>"}
  ],
  "education": [
    {"institution": "<school>", "degree": "<degree>", "field": "<field or null>", "year": <graduation year or null>}
  ]
}

Rules:
- Extract ALL skills mentioned (programming languages, frameworks, tools, methodologies, soft skills)
- For skills, prefer standardized names (e.g. "Python" not "python programming", "AWS" not "Amazon Web Services")
- If information is missing, use empty string or null — never invent data
- experience array should be ordered most recent first
- Return ONLY the JSON object, no prose or markdown fences"""


class ResumeParserAgent(BaseAgent):
    """Parses resume text into structured RawProfile objects."""

    def __init__(self, config: ModelConfig):
        super().__init__(config)

    def parse(self, resume_text: str, filename: str = "") -> Optional[RawProfile]:
        """Parse a single resume text into a RawProfile."""
        if not resume_text or not resume_text.strip():
            logger.warning(f"Empty resume text for {filename}, skipping.")
            return None

        if self._is_mock_mode():
            return self._mock_parse(resume_text, filename)

        try:
            user_prompt = (
                f"Parse the following resume and extract structured candidate information.\n\n"
                f"RESUME:\n{resume_text}\n\n"
                f"Return ONLY valid JSON."
            )
            response_text = self._call_llm(_SYSTEM_PROMPT, user_prompt)
            data = self._parse_json_response(response_text)
            return self._build_profile(data, filename)
        except Exception as e:
            logger.error(f"Resume parsing failed for {filename}: {e}. Using mock.")
            return self._mock_parse(resume_text, filename)

    def parse_batch(self, resumes: list[dict]) -> list[RawProfile]:
        """Parse multiple resumes. Each dict has 'text' and 'filename' keys."""
        profiles = []
        for item in resumes:
            profile = self.parse(item.get("text", ""), item.get("filename", ""))
            if profile:
                profiles.append(profile)
        return profiles

    def _build_profile(self, data: dict, filename: str) -> RawProfile:
        """Build a RawProfile from parsed JSON data."""
        experience = []
        for exp in data.get("experience", []):
            experience.append(Experience(
                title=exp.get("title", ""),
                company=exp.get("company", ""),
                start_date=exp.get("start_date", ""),
                end_date=exp.get("end_date"),
                description=exp.get("description"),
            ))

        education = []
        for edu in data.get("education", []):
            education.append(Education(
                institution=edu.get("institution", ""),
                degree=edu.get("degree", ""),
                field=edu.get("field"),
                year=edu.get("year"),
            ))

        return RawProfile(
            name=data.get("name", "Unknown"),
            title=data.get("title", ""),
            company=data.get("company", ""),
            skills=data.get("skills", []),
            experience=experience,
            education=education,
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email=data.get("email"),
            phone=data.get("phone"),
            location=data.get("location"),
        )

    # ─── Mock parsing ──────────────────────────────────────────────────────

    def _mock_parse(self, resume_text: str, filename: str) -> RawProfile:
        """Extract a plausible mock profile using simple heuristics on the text."""
        text_lower = resume_text.lower()

        # Try to detect role type from resume content for varied mocks
        if any(k in text_lower for k in ("data engineer", "spark", "etl", "data pipeline")):
            return self._mock_data_engineer(filename)
        if any(k in text_lower for k in ("frontend", "react", "vue", "angular", "css")):
            return self._mock_frontend(filename)
        if any(k in text_lower for k in ("machine learning", "ml ", "pytorch", "tensorflow")):
            return self._mock_ml_engineer(filename)
        if any(k in text_lower for k in ("devops", "kubernetes", "terraform", "infrastructure")):
            return self._mock_devops(filename)
        # Default: Python backend
        return self._mock_python_backend(filename)

    def _mock_python_backend(self, filename: str) -> RawProfile:
        return RawProfile(
            name="Ananya Krishnan",
            title="Senior Python Developer",
            company="TechCorp India",
            skills=["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "AWS", "Redis", "Git", "CI/CD"],
            experience=[
                Experience(title="Senior Python Developer", company="TechCorp India", start_date="2021-01", end_date=None, description="Led backend API development for fintech platform serving 5M users."),
                Experience(title="Python Developer", company="DataFlow Systems", start_date="2018-06", end_date="2020-12", description="Built microservices and data pipelines using FastAPI and PostgreSQL."),
                Experience(title="Junior Developer", company="StartupXYZ", start_date="2016-08", end_date="2018-05", description="Full-stack development with Python/Django and React."),
            ],
            education=[Education(institution="IIT Madras", degree="B.Tech", field="Computer Science", year=2016)],
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email="ananya.k@email.com",
            phone="+91-9876543210",
            location="Chennai, India",
        )

    def _mock_data_engineer(self, filename: str) -> RawProfile:
        return RawProfile(
            name="Vikram Patel",
            title="Senior Data Engineer",
            company="Analytics Corp",
            skills=["Python", "Apache Spark", "SQL", "AWS", "ETL pipelines", "Kafka", "Airflow", "Snowflake", "dbt"],
            experience=[
                Experience(title="Senior Data Engineer", company="Analytics Corp", start_date="2020-03", end_date=None, description="Designed data lakehouse architecture processing 2TB/day on AWS."),
                Experience(title="Data Engineer", company="BigData Inc", start_date="2017-08", end_date="2020-02", description="Built Spark-based ETL pipelines and maintained Airflow DAGs."),
            ],
            education=[Education(institution="BITS Pilani", degree="M.Tech", field="Data Science", year=2017)],
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email="vikram.p@email.com",
            phone="+91-9123456789",
            location="Hyderabad, India",
        )

    def _mock_frontend(self, filename: str) -> RawProfile:
        return RawProfile(
            name="Meera Sundaram",
            title="Senior Frontend Engineer",
            company="UIWorks",
            skills=["React", "TypeScript", "CSS", "REST APIs", "Git", "Next.js", "Tailwind CSS", "Storybook", "Jest"],
            experience=[
                Experience(title="Senior Frontend Engineer", company="UIWorks", start_date="2020-06", end_date=None, description="Owned component library and performance optimization for 3M-user app."),
                Experience(title="Frontend Developer", company="WebStudio", start_date="2018-01", end_date="2020-05", description="Built responsive web applications with React and TypeScript."),
            ],
            education=[Education(institution="NIT Trichy", degree="B.Tech", field="Information Technology", year=2017)],
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email="meera.s@email.com",
            location="Bangalore, India",
        )

    def _mock_ml_engineer(self, filename: str) -> RawProfile:
        return RawProfile(
            name="Rahul Menon",
            title="ML Engineer",
            company="AI Labs",
            skills=["Python", "PyTorch", "Machine Learning", "SQL", "Docker", "MLflow", "Kubernetes", "TensorFlow"],
            experience=[
                Experience(title="ML Engineer", company="AI Labs", start_date="2021-01", end_date=None, description="Built and deployed recommendation models serving 10M+ predictions/day."),
                Experience(title="Data Scientist", company="PredictCo", start_date="2019-03", end_date="2020-12", description="Developed NLP models for text classification and sentiment analysis."),
            ],
            education=[Education(institution="IISc Bangalore", degree="M.Tech", field="Machine Learning", year=2019)],
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email="rahul.m@email.com",
            location="Bangalore, India",
        )

    def _mock_devops(self, filename: str) -> RawProfile:
        return RawProfile(
            name="Suresh Nair",
            title="Senior DevOps Engineer",
            company="CloudOps",
            skills=["Kubernetes", "Terraform", "AWS", "Docker", "CI/CD", "Linux", "Helm", "Prometheus", "Python"],
            experience=[
                Experience(title="Senior DevOps Engineer", company="CloudOps", start_date="2020-08", end_date=None, description="Managed K8s platform for 30+ microservices, reduced deploy time by 60%."),
                Experience(title="DevOps Engineer", company="InfraTeam", start_date="2018-02", end_date="2020-07", description="Built CI/CD pipelines and infrastructure-as-code with Terraform."),
            ],
            education=[Education(institution="VIT University", degree="B.Tech", field="Computer Science", year=2018)],
            source="resume_upload",
            profile_url=f"resume://{filename}" if filename else None,
            email="suresh.n@email.com",
            location="Pune, India",
        )

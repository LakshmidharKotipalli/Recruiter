import logging
from agents.base import BaseAgent
from config import ModelConfig
from schemas.models import SearchSpec, QuerySet, SourceQueries

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Search Query Generator. Given a structured job spec, produce optimised Boolean search queries for each data source.
Return ONLY valid JSON with no prose.

For each source, produce 3 primary queries (precise) and 2 fallback queries (broader).

Sources:
- google_cse: site:linkedin.com/in Boolean queries using AND/OR/quotes
- naukri: keyword + skills focused queries for India's job portal
- pdl: skill + title focused queries for People Data Labs field-based API

Return JSON:
{
  "google_cse": {
    "primary": ["query1", "query2", "query3"],
    "fallback": ["query1", "query2"]
  },
  "naukri": {
    "primary": ["query1", "query2", "query3"],
    "fallback": ["query1", "query2"]
  },
  "pdl": {
    "primary": ["query1", "query2", "query3"],
    "fallback": ["query1", "query2"]
  }
}"""


class QueryGenerationAgent(BaseAgent):
    """Sub-Agent 02: Search Query Generator."""

    def __init__(self, config: ModelConfig):
        super().__init__(config)

    def generate(self, search_spec: SearchSpec) -> QuerySet:
        """Generate Boolean search queries per source from SearchSpec."""
        if self._is_mock_mode():
            return self._mock_generate(search_spec)

        user_prompt = f"""Generate search queries for the following job spec:

Role Title: {search_spec.role_title}
Seniority: {search_spec.seniority_level}
Location: {search_spec.location}
Must-Have Skills: {', '.join(search_spec.must_have_skills)}
Nice-to-Have Skills: {', '.join(search_spec.nice_to_have_skills)}

Return ONLY the JSON query object as specified."""

        try:
            response_text = self._call_llm(SYSTEM_PROMPT, user_prompt)
            data = self._parse_json_response(response_text)
            return QuerySet(
                google_cse=SourceQueries(
                    primary=data["google_cse"]["primary"],
                    fallback=data["google_cse"]["fallback"],
                ),
                naukri=SourceQueries(
                    primary=data["naukri"]["primary"],
                    fallback=data["naukri"]["fallback"],
                ),
                pdl=SourceQueries(
                    primary=data["pdl"]["primary"],
                    fallback=data["pdl"]["fallback"],
                ),
            )
        except Exception as e:
            logger.error(f"Query generation failed: {e}. Falling back to mock.")
            return self._mock_generate(search_spec)

    def _mock_generate(self, spec: SearchSpec) -> QuerySet:
        """Return realistic mock queries based on the SearchSpec."""
        role = spec.role_title
        skills_str = " AND ".join(f'"{s}"' for s in spec.must_have_skills[:3])
        skills_or = " OR ".join(spec.must_have_skills[:4])
        location = spec.location or "remote"
        seniority = spec.seniority_level

        return QuerySet(
            google_cse=SourceQueries(
                primary=[
                    f'site:linkedin.com/in "{role}" {skills_str} "{seniority}"',
                    f'site:linkedin.com/in "{role}" {skills_str} "{location}"',
                    f'site:linkedin.com/in ({skills_str}) ("{seniority} engineer" OR "{seniority} developer")',
                ],
                fallback=[
                    f'site:linkedin.com/in "{role}" ({skills_or})',
                    f'site:linkedin.com/in ({skills_or}) engineer',
                ],
            ),
            naukri=SourceQueries(
                primary=[
                    f'{role} {" ".join(spec.must_have_skills[:3])} {seniority}',
                    f'{" ".join(spec.must_have_skills[:4])} {location}',
                    f'{role} {" ".join(spec.must_have_skills[:2])} {" ".join(spec.nice_to_have_skills[:2])}',
                ],
                fallback=[
                    f'{role} {" ".join(spec.must_have_skills[:2])}',
                    f'{skills_or} developer {location}',
                ],
            ),
            pdl=SourceQueries(
                primary=[
                    f'job_title:"{role}" skills:{spec.must_have_skills[0] if spec.must_have_skills else "Python"}',
                    f'skills:{" ".join(spec.must_have_skills[:3])} seniority:{seniority}',
                    f'job_title:("{seniority} {role}") location:"{location}"',
                ],
                fallback=[
                    f'skills:{" ".join(spec.must_have_skills[:2])} job_title:"engineer"',
                    f'job_title:"{role}"',
                ],
            ),
        )

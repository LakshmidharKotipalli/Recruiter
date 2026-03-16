import logging
from typing import Optional
from schemas.models import PipelineInput, PipelineOutput
from agents.jd_extraction import JDExtractionAgent
from agents.query_generation import QueryGenerationAgent
from agents.profile_fetching import ProfileFetchingAgent
from agents.deduplication import DeduplicationStage
from agents.ranking import RankingAgent
from agents.resume_parser import ResumeParserAgent
from agents.orchestrator import OrchestratorAgent
from agents.event_bus import EventBus
from agents.hitl import HITLStore
from integrations.google_cse import GoogleCSEIntegration
from integrations.naukri import NaukriIntegration
from integrations.pdl import PDLIntegration
from config import APP_CONFIG, AppConfig

logger = logging.getLogger(__name__)


class AgenticPipeline:
    """Primary pipeline: OrchestratorAgent with tool_use, feedback loops, and HITL.

    Creates a fresh OrchestratorAgent per request to avoid shared mutable state
    between concurrent runs.
    """

    def __init__(self, config: AppConfig = APP_CONFIG):
        self.config = config

    def _build_orchestrator(self) -> OrchestratorAgent:
        """Build a fresh orchestrator with all sub-agents for a single run."""
        model_cfg = self.config.model
        sources_cfg = self.config.sources

        jd_agent = JDExtractionAgent(model_cfg)
        query_agent = QueryGenerationAgent(model_cfg)
        dedup_stage = DeduplicationStage()
        ranking_agent = RankingAgent(model_cfg)

        integrations = {
            "google_cse": GoogleCSEIntegration(
                sources_cfg["google_cse"].api_key, self.config.mock_mode
            ),
            "naukri": NaukriIntegration(
                sources_cfg["naukri"].api_key, self.config.mock_mode
            ),
            "pdl": PDLIntegration(
                sources_cfg["pdl"].api_key, self.config.mock_mode
            ),
        }
        fetching_agent = ProfileFetchingAgent(integrations)

        return OrchestratorAgent(
            config=model_cfg,
            jd_agent=jd_agent,
            dedup_stage=dedup_stage,
            ranking_agent=ranking_agent,
            query_agent=query_agent,
            fetching_agent=fetching_agent,
        )

    async def run(
        self,
        pipeline_input: PipelineInput,
        event_bus: Optional[EventBus] = None,
        hitl_store: Optional[HITLStore] = None,
        run_id: Optional[str] = None,
    ) -> PipelineOutput:
        orchestrator = self._build_orchestrator()
        return await orchestrator.run(
            pipeline_input,
            event_bus=event_bus,
            hitl_store=hitl_store,
            run_id=run_id,
        )


class ResumeMatchPipeline:
    """Agentic pipeline for matching uploaded resumes against a job description.

    Flow: extract_jd → parse_resumes → score → assess → (relax → re-score loop).
    Uses the same OrchestratorAgent as the sourcing pipeline but in resume mode,
    with feedback loops (relax_requirements) and HITL-1 (JD review).
    """

    def __init__(self, config: AppConfig = APP_CONFIG):
        self.config = config

    def _build_orchestrator(self) -> OrchestratorAgent:
        """Build an orchestrator configured for resume-match mode."""
        model_cfg = self.config.model

        return OrchestratorAgent(
            config=model_cfg,
            jd_agent=JDExtractionAgent(model_cfg),
            dedup_stage=DeduplicationStage(),
            ranking_agent=RankingAgent(model_cfg),
            resume_parser=ResumeParserAgent(model_cfg),
        )

    async def run(
        self,
        pipeline_input: PipelineInput,
        resumes: list[dict],
        event_bus: Optional[EventBus] = None,
        hitl_store: Optional[HITLStore] = None,
        run_id: Optional[str] = None,
    ) -> PipelineOutput:
        """Run the agentic resume-match pipeline.

        Args:
            pipeline_input: JD text + recruiter config.
            resumes: List of dicts with 'text' and 'filename' keys.
            event_bus: Optional EventBus for SSE streaming.
            hitl_store: Optional HITLStore for HITL checkpoints.
            run_id: Optional run ID for HITL correlation.
        """
        orchestrator = self._build_orchestrator()
        return await orchestrator.run(
            pipeline_input,
            event_bus=event_bus,
            hitl_store=hitl_store,
            run_id=run_id,
            resumes=resumes,
        )

"""
OrchestratorAgent — drives the agentic sourcing pipeline.

Both mock and live mode go through the same agentic loop:
  - Live mode: Anthropic tool_use ReAct loop (LLM decides which tool to call)
  - Mock mode: Deterministic state-machine generates tool calls (no LLM needed)

Supports two modes of operation:
  - Sourcing mode: extract → queries → fetch → score → assess → (refine loop)
  - Resume mode:   extract → parse_resumes → score → assess → (relax → re-score loop)

The tool dispatch, HITL checkpoints, trace recording, and refinement logic are
identical in both modes — only the tool-call decision maker differs.

State is isolated per-run via a _RunContext dataclass to prevent concurrent
request interference.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from agents.base import BaseAgent
from agents.event_bus import EventBus
from agents.hitl import HITLStore
from agents.quality_checker import QualityChecker
from agents.tools import PIPELINE_TOOLS, RESUME_PIPELINE_TOOLS
from config import APP_CONFIG, ModelConfig
from schemas.models import (
    ExecutionTrace,
    HITLDecision,
    PipelineInput,
    PipelineOutput,
    QuerySet,
    RecruiterConfig,
    SearchSpec,
    TraceStep,
    WeightConfig,
)

logger = logging.getLogger(__name__)

_ORCHESTRATOR_SYSTEM = """You are an intelligent recruiting pipeline orchestrator.
Your job is to source high-quality candidates for a given job description using the available tools.

Available tools (call them in this order):
1. extract_jd        — parse and enrich the JD into a structured spec
2. generate_queries  — build search queries for all talent sources
3. fetch_profiles    — fetch raw candidate profiles from all sources
4. score_candidates  — deduplicate + score candidates against the spec
5. assess_quality    — evaluate the shortlist; if it is thin, refine and loop

Rules:
- Always start with extract_jd.
- After fetch_profiles, always call score_candidates, then assess_quality.
- If assess_quality recommends refine_broader → call generate_queries with strategy="broader", then fetch_profiles, score_candidates, assess_quality again.
- If assess_quality recommends refine_location_relaxed → call generate_queries with strategy="location_relaxed", then fetch_profiles, score_candidates, assess_quality again.
- If assess_quality recommends accept or force_accept → STOP.
- Maximum 3 full search iterations.
- Do not add prose between tool calls — just call the next tool."""

_RESUME_ORCHESTRATOR_SYSTEM = """You are an intelligent recruiting pipeline orchestrator working in resume-match mode.
Your job is to evaluate uploaded resumes against a job description using the available tools.

Available tools (call them in this order):
1. extract_jd          — parse and enrich the JD into a structured spec
2. parse_resumes       — parse uploaded resume files into structured profiles
3. score_candidates    — deduplicate + score candidates against the spec
4. assess_quality      — evaluate the shortlist quality
5. relax_requirements  — relax search criteria if too few candidates match

Rules:
- Always start with extract_jd, then parse_resumes.
- After parse_resumes, call score_candidates, then assess_quality.
- If assess_quality recommends refine_broader → call relax_requirements with strategy="broader", then score_candidates, assess_quality again.
- If assess_quality recommends refine_location_relaxed → call relax_requirements with strategy="location_relaxed", then score_candidates, assess_quality again.
- If assess_quality recommends accept or force_accept → STOP.
- Maximum 3 scoring iterations.
- Do not add prose between tool calls — just call the next tool."""


# ── Mock agent step constants ────────────────────────────────────────────────
# Sourcing mode:
#   0=extract_jd → 1=generate_queries → 2=fetch_profiles → 3=score_candidates → 4=assess_quality
#   After assess: if refine → back to 1, if accept → done
_STEP_EXTRACT = 0
_STEP_QUERIES = 1
_STEP_FETCH = 2
_STEP_SCORE = 3
_STEP_ASSESS = 4

# Resume mode:
#   0=extract_jd → 5=parse_resumes → 3=score_candidates → 4=assess_quality
#   After assess: if refine → 6=relax_requirements → 3=score → 4=assess
_STEP_PARSE = 5
_STEP_RELAX = 6


@dataclass
class _RunContext:
    """Per-run mutable state. Created fresh for every pipeline invocation."""
    search_spec: Optional[SearchSpec] = None
    query_set: Optional[QuerySet] = None
    raw_profiles: list = field(default_factory=list)
    source_availability: dict = field(default_factory=dict)
    scored_profiles: list = field(default_factory=list)
    deduped_count: int = 0
    recruiter_config: Optional[RecruiterConfig] = None
    trace: ExecutionTrace = field(default_factory=ExecutionTrace)
    iteration: int = 0
    event_bus: Optional[EventBus] = None
    hitl_store: Optional[HITLStore] = None
    run_id: Optional[str] = None
    # Resume mode
    resumes: list = field(default_factory=list)
    resume_mode: bool = False
    # Mock agent state
    mock_step: int = _STEP_EXTRACT
    last_quality_action: Optional[str] = None


class OrchestratorAgent(BaseAgent):
    """Wraps all pipeline stages as tool-callable steps with feedback loops and HITL."""

    def __init__(
        self,
        config: ModelConfig,
        jd_agent,
        dedup_stage,
        ranking_agent,
        query_agent=None,
        fetching_agent=None,
        resume_parser=None,
    ):
        super().__init__(config)
        self.jd_agent = jd_agent
        self.query_agent = query_agent
        self.fetching_agent = fetching_agent
        self.dedup_stage = dedup_stage
        self.ranking_agent = ranking_agent
        self.resume_parser = resume_parser
        self.quality_checker = QualityChecker()

        pipeline_cfg = APP_CONFIG.pipeline
        self._hitl_min_raw = pipeline_cfg.hitl_min_raw
        self._force_accept_top_n = pipeline_cfg.force_accept_top_n
        self._hitl_timeout = pipeline_cfg.hitl_timeout_seconds

    # ─── Public entry point ──────────────────────────────────────────────────

    async def run(
        self,
        pipeline_input: PipelineInput,
        event_bus: Optional[EventBus] = None,
        hitl_store: Optional[HITLStore] = None,
        run_id: Optional[str] = None,
        resumes: Optional[list] = None,
    ) -> PipelineOutput:
        """Run the agentic pipeline. Same code path for mock and live mode."""
        resume_mode = bool(resumes)
        ctx = _RunContext(
            recruiter_config=pipeline_input.config,
            event_bus=event_bus,
            hitl_store=hitl_store,
            run_id=run_id,
            resumes=resumes or [],
            resume_mode=resume_mode,
        )

        if resume_mode:
            # In resume mode, first step after extract is parse
            ctx.mock_step = _STEP_EXTRACT
            ctx.source_availability = {"resume_upload": True}

        mode_label = "resume_match" if resume_mode else "sourcing"
        self._emit(ctx, "pipeline_start", {
            "run_id": run_id,
            "jd_length": len(pipeline_input.raw_jd),
            "mode": mode_label,
            "resume_count": len(ctx.resumes) if resume_mode else 0,
        })
        result = await self._agentic_run(pipeline_input, ctx)
        self._emit(ctx, "pipeline_complete", result.model_dump())
        return result

    # ─── Agentic loop (unified for mock and live) ─────────────────────────────

    async def _agentic_run(self, pipeline_input: PipelineInput, ctx: _RunContext) -> PipelineOutput:
        """
        Unified agentic loop.

        In live mode: tool calls come from Anthropic tool_use API.
        In mock mode: tool calls come from a deterministic state machine.
        """
        mock_mode = self._is_mock_mode()
        cfg = pipeline_input.config

        # Select tools and system prompt based on mode
        if ctx.resume_mode:
            tools = RESUME_PIPELINE_TOOLS
            system_prompt = _RESUME_ORCHESTRATOR_SYSTEM
            user_msg = (
                f"Match these uploaded resumes against the job description.\n\n"
                f"JD:\n{pipeline_input.raw_jd}\n\n"
                f"Recruiter config:\n{cfg.model_dump_json()}\n\n"
                f"Number of resumes uploaded: {len(ctx.resumes)}"
            )
        else:
            tools = PIPELINE_TOOLS
            system_prompt = _ORCHESTRATOR_SYSTEM
            user_msg = (
                f"Source candidates for this job description.\n\n"
                f"JD:\n{pipeline_input.raw_jd}\n\n"
                f"Recruiter config:\n{cfg.model_dump_json()}"
            )

        messages = [{"role": "user", "content": user_msg}]
        llm_call_count = 0
        final_action = "accept"

        while True:
            # ── Decide next tool calls ──
            if mock_mode:
                tool_calls = self._mock_next_tools(ctx, pipeline_input)
            else:
                _text, tool_calls = self._call_llm_with_tools(
                    tools=tools,
                    messages=messages,
                    system=system_prompt,
                )

            llm_call_count += 1
            ctx.trace.total_llm_calls = llm_call_count if not mock_mode else 0

            if not tool_calls:
                break

            # ── Build assistant message (for LLM context in live mode) ──
            assistant_content = []
            for tc in tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # ── Dispatch each tool call ──
            tool_results = []
            for tc in tool_calls:
                result_str = await self._dispatch_tool(tc["name"], tc["input"], cfg, ctx)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

            # ── Check if assess_quality said to stop ──
            for tc in tool_calls:
                if tc["name"] == "assess_quality":
                    assessment = self.quality_checker.assess(
                        accepted_count=tc["input"]["accepted_count"],
                        close_count=tc["input"]["close_count"],
                        total_raw=tc["input"]["total_raw"],
                        iteration=tc["input"]["iteration"],
                        jd_quality_score=tc["input"].get("jd_quality_score", 10),
                    )
                    final_action = assessment.recommended_action
                    ctx.last_quality_action = final_action

                    if assessment.accept:
                        ctx.trace.final_action = final_action
                        ctx.trace.total_iterations = tc["input"]["iteration"]
                        return self._build_output(ctx, assessment)

        # Fallback: build output from whatever state we have
        ctx.trace.final_action = final_action
        accepted = [p for p in ctx.scored_profiles if p.bucket == "accepted"]
        close = [p for p in ctx.scored_profiles if p.bucket == "close"]
        assessment = self.quality_checker.assess(
            accepted_count=len(accepted),
            close_count=len(close),
            total_raw=len(ctx.raw_profiles),
            iteration=max(ctx.iteration, 1),
            jd_quality_score=getattr(ctx.search_spec, "jd_quality_score", 10) if ctx.search_spec else 10,
        )
        return self._build_output(ctx, assessment)

    # ─── Mock tool-call generators (state machines) ──────────────────────────

    def _mock_next_tools(
        self, ctx: _RunContext, pipeline_input: PipelineInput
    ) -> list[dict]:
        """Route to the correct mock state machine based on mode."""
        if ctx.resume_mode:
            return self._mock_next_tools_resume(ctx, pipeline_input)
        return self._mock_next_tools_sourcing(ctx, pipeline_input)

    def _mock_next_tools_sourcing(
        self, ctx: _RunContext, pipeline_input: PipelineInput
    ) -> list[dict]:
        """
        Deterministic state machine for sourcing mode.

        Sequence: extract → queries → fetch → score → assess → (refine loop or stop)
        """
        step = ctx.mock_step
        tid = f"mock_{uuid.uuid4().hex[:8]}"

        if step == _STEP_EXTRACT:
            ctx.mock_step = _STEP_QUERIES
            return [{
                "name": "extract_jd",
                "id": tid,
                "input": {
                    "raw_jd": pipeline_input.raw_jd,
                    "recruiter_config_json": pipeline_input.config.model_dump_json(),
                },
            }]

        if step == _STEP_QUERIES:
            if ctx.search_spec is None:
                logger.error("Mock agent: search_spec is None at generate_queries step — aborting loop")
                return []
            strategy = "precise"
            if ctx.last_quality_action == "refine_broader":
                strategy = "broader"
            elif ctx.last_quality_action == "refine_location_relaxed":
                strategy = "location_relaxed"

            ctx.mock_step = _STEP_FETCH
            return [{
                "name": "generate_queries",
                "id": tid,
                "input": {
                    "search_spec_json": ctx.search_spec.model_dump_json(),
                    "strategy": strategy,
                },
            }]

        if step == _STEP_FETCH:
            if ctx.query_set is None:
                logger.error("Mock agent: query_set is None at fetch_profiles step — aborting loop")
                return []
            ctx.mock_step = _STEP_SCORE
            return [{
                "name": "fetch_profiles",
                "id": tid,
                "input": {
                    "query_set_json": ctx.query_set.model_dump_json(),
                    "use_fallback_queries": ctx.last_quality_action is not None,
                },
            }]

        if step == _STEP_SCORE:
            if ctx.search_spec is None:
                logger.error("Mock agent: search_spec is None at score_candidates step — aborting loop")
                return []
            ctx.mock_step = _STEP_ASSESS
            return [{
                "name": "score_candidates",
                "id": tid,
                "input": {
                    "search_spec_json": ctx.search_spec.model_dump_json(),
                    "recruiter_config_json": ctx.recruiter_config.model_dump_json() if ctx.recruiter_config else "{}",
                },
            }]

        if step == _STEP_ASSESS:
            accepted = [p for p in ctx.scored_profiles if p.bucket == "accepted"]
            close = [p for p in ctx.scored_profiles if p.bucket == "close"]
            # After assess, reset to queries step for potential refinement loop
            ctx.mock_step = _STEP_QUERIES
            return [{
                "name": "assess_quality",
                "id": tid,
                "input": {
                    "accepted_count": len(accepted),
                    "close_count": len(close),
                    "total_raw": len(ctx.raw_profiles),
                    "iteration": ctx.iteration,
                    "jd_quality_score": ctx.search_spec.jd_quality_score if ctx.search_spec else 10,
                },
            }]

        return []

    def _mock_next_tools_resume(
        self, ctx: _RunContext, pipeline_input: PipelineInput
    ) -> list[dict]:
        """
        Deterministic state machine for resume-match mode.

        Sequence: extract → parse_resumes → score → assess → (relax → re-score loop)
        """
        step = ctx.mock_step
        tid = f"mock_{uuid.uuid4().hex[:8]}"

        if step == _STEP_EXTRACT:
            ctx.mock_step = _STEP_PARSE
            return [{
                "name": "extract_jd",
                "id": tid,
                "input": {
                    "raw_jd": pipeline_input.raw_jd,
                    "recruiter_config_json": pipeline_input.config.model_dump_json(),
                },
            }]

        if step == _STEP_PARSE:
            ctx.mock_step = _STEP_SCORE
            return [{
                "name": "parse_resumes",
                "id": tid,
                "input": {
                    "resume_count": len(ctx.resumes),
                },
            }]

        if step == _STEP_SCORE:
            if ctx.search_spec is None:
                logger.error("Mock agent: search_spec is None at score_candidates step — aborting loop")
                return []
            ctx.mock_step = _STEP_ASSESS
            return [{
                "name": "score_candidates",
                "id": tid,
                "input": {
                    "search_spec_json": ctx.search_spec.model_dump_json(),
                    "recruiter_config_json": ctx.recruiter_config.model_dump_json() if ctx.recruiter_config else "{}",
                },
            }]

        if step == _STEP_ASSESS:
            accepted = [p for p in ctx.scored_profiles if p.bucket == "accepted"]
            close = [p for p in ctx.scored_profiles if p.bucket == "close"]
            # After assess, go to relax step for potential refinement
            ctx.mock_step = _STEP_RELAX
            return [{
                "name": "assess_quality",
                "id": tid,
                "input": {
                    "accepted_count": len(accepted),
                    "close_count": len(close),
                    "total_raw": len(ctx.raw_profiles),
                    "iteration": ctx.iteration,
                    "jd_quality_score": ctx.search_spec.jd_quality_score if ctx.search_spec else 10,
                },
            }]

        if step == _STEP_RELAX:
            # Only reached when quality checker didn't accept
            strategy = "broader"
            if ctx.last_quality_action == "refine_location_relaxed":
                strategy = "location_relaxed"
            ctx.mock_step = _STEP_SCORE
            return [{
                "name": "relax_requirements",
                "id": tid,
                "input": {
                    "strategy": strategy,
                },
            }]

        return []

    # ─── Tool dispatcher ──────────────────────────────────────────────────────

    async def _dispatch_tool(
        self, name: str, inputs: dict, cfg: RecruiterConfig, ctx: _RunContext
    ) -> str:
        """Execute a single tool call and return the result as a JSON string."""
        self._emit(ctx, "tool_call", {"tool": name, "iteration": ctx.iteration + 1})

        try:
            if name == "extract_jd":
                return await self._tool_extract_jd(inputs, cfg, ctx)
            elif name == "generate_queries":
                return await self._tool_generate_queries(inputs, ctx)
            elif name == "fetch_profiles":
                return await self._tool_fetch_profiles(inputs, ctx)
            elif name == "score_candidates":
                return await self._tool_score_candidates(inputs, cfg, ctx)
            elif name == "assess_quality":
                return await self._tool_assess_quality(inputs, ctx)
            elif name == "parse_resumes":
                return await self._tool_parse_resumes(inputs, ctx)
            elif name == "relax_requirements":
                return await self._tool_relax_requirements(inputs, ctx)
            else:
                return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as e:
            logger.exception(f"Tool {name} failed: {e}")
            return json.dumps({"error": str(e)})

    async def _tool_extract_jd(
        self, inputs: dict, cfg: RecruiterConfig, ctx: _RunContext
    ) -> str:
        raw_jd = inputs["raw_jd"]
        try:
            recruiter_cfg = RecruiterConfig.model_validate_json(inputs["recruiter_config_json"])
        except Exception:
            recruiter_cfg = cfg

        ctx.search_spec = self.jd_agent.extract(raw_jd, recruiter_cfg)
        ctx.trace.steps.append(TraceStep(
            tool="extract_jd", iteration=ctx.iteration + 1,
            input_keys=list(inputs.keys()),
            result_summary=f"role={ctx.search_spec.role_title}, quality={ctx.search_spec.jd_quality_score}/10",
        ))
        self._emit(ctx, "agent_complete", {
            "stage": "jd_extraction",
            "result": {
                "role_title": ctx.search_spec.role_title,
                "jd_quality_score": ctx.search_spec.jd_quality_score,
                "must_have_count": len(ctx.search_spec.must_have_skills),
            },
        })

        # HITL-1: recruiter reviews extracted spec
        await self._hitl_jd_review(ctx)

        return ctx.search_spec.model_dump_json()

    async def _tool_generate_queries(self, inputs: dict, ctx: _RunContext) -> str:
        try:
            spec = SearchSpec.model_validate_json(inputs["search_spec_json"])
        except Exception:
            spec = ctx.search_spec

        strategy = inputs.get("strategy", "precise")
        if strategy != "precise":
            ctx.trace.refinement_reasons.append(
                f"Iteration {ctx.iteration + 1}: query strategy changed to '{strategy}'"
            )
            self._emit(ctx, "refinement", {"strategy": strategy, "iteration": ctx.iteration + 1})

        ctx.query_set = self.query_agent.generate(spec)
        ctx.trace.steps.append(TraceStep(
            tool="generate_queries", iteration=ctx.iteration + 1,
            input_keys=list(inputs.keys()),
            result_summary=f"strategy={strategy}, {len(ctx.query_set.google_cse.primary)} CSE queries",
        ))
        return ctx.query_set.model_dump_json()

    async def _tool_fetch_profiles(self, inputs: dict, ctx: _RunContext) -> str:
        try:
            query_set = QuerySet.model_validate_json(inputs["query_set_json"])
        except Exception:
            query_set = ctx.query_set

        use_fallback = inputs.get("use_fallback_queries", False)
        ctx.raw_profiles, ctx.source_availability = await self.fetching_agent.fetch_all(
            query_set
        )
        ctx.trace.steps.append(TraceStep(
            tool="fetch_profiles", iteration=ctx.iteration + 1,
            input_keys=list(inputs.keys()),
            result_summary=f"raw_count={len(ctx.raw_profiles)}, fallback={use_fallback}",
        ))
        self._emit(ctx, "agent_complete", {
            "stage": "profile_fetching",
            "result": {"raw_count": len(ctx.raw_profiles)},
        })

        # HITL-2: thin pool check
        await self._hitl_profile_count(ctx)

        return json.dumps({
            "raw_count": len(ctx.raw_profiles),
            "source_availability": ctx.source_availability,
        })

    async def _tool_parse_resumes(self, inputs: dict, ctx: _RunContext) -> str:
        """Parse uploaded resumes into RawProfile objects."""
        if not self.resume_parser:
            return json.dumps({"error": "Resume parser not available"})

        ctx.raw_profiles = self.resume_parser.parse_batch(ctx.resumes)
        ctx.trace.steps.append(TraceStep(
            tool="parse_resumes", iteration=ctx.iteration + 1,
            input_keys=list(inputs.keys()),
            result_summary=f"parsed={len(ctx.raw_profiles)}/{len(ctx.resumes)} resumes",
        ))
        self._emit(ctx, "agent_complete", {
            "stage": "resume_parsing",
            "result": {
                "parsed_count": len(ctx.raw_profiles),
                "total_uploaded": len(ctx.resumes),
            },
        })
        return json.dumps({
            "parsed_count": len(ctx.raw_profiles),
            "total_uploaded": len(ctx.resumes),
        })

    async def _tool_relax_requirements(self, inputs: dict, ctx: _RunContext) -> str:
        """Relax search requirements to accept more candidates from resumes."""
        if not ctx.search_spec:
            return json.dumps({"error": "No search spec to relax"})

        strategy = inputs.get("strategy", "broader")
        changes = []

        if strategy == "broader" and ctx.search_spec.must_have_skills:
            demoted = ctx.search_spec.must_have_skills.pop()
            ctx.search_spec.nice_to_have_skills.append(demoted)
            changes.append(f"Moved '{demoted}' from must-have to nice-to-have")
        elif strategy == "location_relaxed" and ctx.search_spec.location:
            old_loc = ctx.search_spec.location
            ctx.search_spec.location = ""
            changes.append(f"Cleared location constraint (was '{old_loc}')")

        summary = "; ".join(changes) if changes else "No changes applied"
        ctx.trace.steps.append(TraceStep(
            tool="relax_requirements", iteration=ctx.iteration,
            input_keys=list(inputs.keys()),
            result_summary=f"strategy={strategy}, {summary}",
        ))
        ctx.trace.refinement_reasons.append(f"Iteration {ctx.iteration}: {summary}")
        self._emit(ctx, "refinement", {"strategy": strategy, "changes": changes})

        return json.dumps({
            "strategy": strategy,
            "changes": changes,
        })

    async def _tool_score_candidates(
        self, inputs: dict, cfg: RecruiterConfig, ctx: _RunContext
    ) -> str:
        try:
            spec = SearchSpec.model_validate_json(inputs["search_spec_json"])
        except Exception:
            spec = ctx.search_spec
        try:
            r_cfg = RecruiterConfig.model_validate_json(inputs["recruiter_config_json"])
        except Exception:
            r_cfg = cfg

        ctx.iteration += 1
        deduped = self.dedup_stage.deduplicate(ctx.raw_profiles)
        ctx.deduped_count = len(deduped)
        ctx.scored_profiles = self.ranking_agent.rank(deduped, spec, r_cfg)

        accepted = [p for p in ctx.scored_profiles if p.bucket == "accepted"]
        close = [p for p in ctx.scored_profiles if p.bucket == "close"]
        ctx.trace.steps.append(TraceStep(
            tool="score_candidates", iteration=ctx.iteration,
            input_keys=list(inputs.keys()),
            result_summary=f"accepted={len(accepted)}, close={len(close)}",
        ))
        self._emit(ctx, "agent_complete", {
            "stage": "scoring",
            "result": {"accepted": len(accepted), "close": len(close)},
        })

        return json.dumps({
            "accepted_count": len(accepted),
            "close_count": len(close),
            "total_deduped": len(deduped),
        })

    async def _tool_assess_quality(self, inputs: dict, ctx: _RunContext) -> str:
        assessment = self.quality_checker.assess(
            accepted_count=inputs["accepted_count"],
            close_count=inputs["close_count"],
            total_raw=inputs["total_raw"],
            iteration=inputs["iteration"],
            jd_quality_score=inputs.get("jd_quality_score", 10),
        )
        ctx.trace.steps.append(TraceStep(
            tool="assess_quality", iteration=inputs["iteration"],
            input_keys=list(inputs.keys()),
            result_summary=f"action={assessment.recommended_action}",
        ))
        self._emit(ctx, "quality_assessed", {
            "action": assessment.recommended_action,
            "reason": assessment.reason,
        })
        return json.dumps({
            "recommended_action": assessment.recommended_action,
            "reason": assessment.reason,
            "accept": assessment.accept,
        })

    # ─── HITL helpers (unified for mock and live) ─────────────────────────────

    async def _hitl_jd_review(self, ctx: _RunContext) -> None:
        """Pause for recruiter to review/edit the extracted SearchSpec.

        In mock mode: emits the event, waits briefly for UI demo, auto-approves.
        In live mode: waits for recruiter to POST /resume with overrides.
        """
        if not ctx.search_spec:
            return

        self._emit(ctx, "hitl_jd_review", {
            "run_id": ctx.run_id,
            "search_spec": ctx.search_spec.model_dump(),
            "message": "Please review the extracted job specification before search begins.",
        })

        if self._is_mock_mode():
            await asyncio.sleep(0.5)
            ctx.trace.hitl_decisions.append(
                "HITL-1 (JD review): auto-approved in mock mode"
            )
            self._emit(ctx, "hitl_resumed", {"checkpoint": "jd_review", "auto": True})
            return

        # Live mode: wait for recruiter response
        if not ctx.hitl_store or not ctx.run_id:
            return

        ctx.hitl_store.create(ctx.run_id)
        decision_raw = await ctx.hitl_store.get(ctx.run_id).wait(timeout=self._hitl_timeout)
        ctx.hitl_store.delete(ctx.run_id)

        if decision_raw:
            try:
                decision = HITLDecision.model_validate(decision_raw)
                self._apply_spec_overrides(decision.spec_overrides, ctx)
            except Exception as e:
                logger.warning(f"Invalid HITL-1 decision payload: {e}")

        self._emit(ctx, "hitl_resumed", {"checkpoint": "jd_review"})

    async def _hitl_profile_count(self, ctx: _RunContext) -> None:
        """Pause for recruiter if raw profile count is below threshold.

        In mock mode: emits the event, waits briefly, auto-proceeds.
        In live mode: waits for recruiter to POST /resume with decision.
        """
        if len(ctx.raw_profiles) >= self._hitl_min_raw:
            return

        self._emit(ctx, "hitl_profile_count", {
            "run_id": ctx.run_id,
            "raw_count": len(ctx.raw_profiles),
            "threshold": self._hitl_min_raw,
            "message": f"Only {len(ctx.raw_profiles)} profiles found. How should we proceed?",
        })

        if self._is_mock_mode():
            await asyncio.sleep(0.5)
            ctx.trace.hitl_decisions.append(
                f"HITL-2 (thin pool: {len(ctx.raw_profiles)} profiles): auto-proceeded in mock mode"
            )
            self._emit(ctx, "hitl_resumed", {"checkpoint": "profile_count", "auto": True})
            return

        # Live mode: wait for recruiter response
        if not ctx.hitl_store or not ctx.run_id:
            return

        hitl_key = f"{ctx.run_id}_profile_count"
        ctx.hitl_store.create(hitl_key)
        decision_raw = await ctx.hitl_store.get(hitl_key).wait(timeout=self._hitl_timeout)
        ctx.hitl_store.delete(hitl_key)

        decision_str = "proceed"
        if decision_raw:
            try:
                d = HITLDecision.model_validate(decision_raw)
                decision_str = d.decision
            except Exception:
                pass

        summary = f"HITL-2 (thin pool: {len(ctx.raw_profiles)} profiles): recruiter chose '{decision_str}'"
        ctx.trace.hitl_decisions.append(summary)
        self._emit(ctx, "hitl_resumed", {"checkpoint": "profile_count", "decision": decision_str})

    def _apply_spec_overrides(self, overrides: dict, ctx: _RunContext) -> None:
        """Apply recruiter edits to the current SearchSpec with type validation."""
        if not overrides or not ctx.search_spec:
            return

        valid_fields = set(ctx.search_spec.model_fields.keys())
        changes = []
        for field_name, value in overrides.items():
            if field_name not in valid_fields:
                logger.warning(f"HITL override ignored unknown field: {field_name}")
                continue

            expected_type = type(getattr(ctx.search_spec, field_name))
            if not isinstance(value, expected_type):
                try:
                    test_data = ctx.search_spec.model_dump()
                    test_data[field_name] = value
                    SearchSpec.model_validate(test_data)
                except Exception as e:
                    logger.warning(
                        f"HITL override rejected for field '{field_name}': {e}"
                    )
                    continue

            old_val = getattr(ctx.search_spec, field_name)
            setattr(ctx.search_spec, field_name, value)
            changes.append(f"{field_name}: {old_val!r} → {value!r}")

        if changes:
            summary = f"HITL-1 (JD review): recruiter updated {', '.join(changes)}"
            ctx.trace.hitl_decisions.append(summary)
            logger.info(f"HITL-1 spec overrides applied: {changes}")

    # ─── Output assembly ──────────────────────────────────────────────────────

    def _build_output(self, ctx: _RunContext, assessment) -> PipelineOutput:
        """Assemble PipelineOutput; promote close → accepted on force_accept."""
        accepted = [p for p in ctx.scored_profiles if p.bucket == "accepted"]
        close = [p for p in ctx.scored_profiles if p.bucket == "close"]
        rejected = [p for p in ctx.scored_profiles if p.bucket == "rejected"]

        if assessment.recommended_action == "force_accept" and not accepted:
            top_close = sorted(close, key=lambda p: p.score_pct, reverse=True)[:self._force_accept_top_n]
            promoted_set = set(id(p) for p in top_close)
            accepted = [p.model_copy(update={"bucket": "accepted"}) for p in top_close]
            close = [p for p in close if id(p) not in promoted_set]
            ctx.trace.hitl_decisions.append(
                f"force_accept: promoted top-{len(top_close)} close candidates to accepted"
            )

        return PipelineOutput(
            accepted=accepted,
            close=close,
            rejected_count=len(rejected),
            source_availability=ctx.source_availability,
            search_spec=ctx.search_spec or SearchSpec(
                role_title="Unknown",
                must_have_skills=[],
                nice_to_have_skills=[],
                seniority_level="mid",
                location="",
                weights=ctx.recruiter_config.weights if ctx.recruiter_config else WeightConfig(),
            ),
            total_raw=len(ctx.raw_profiles),
            total_deduped=ctx.deduped_count,
            execution_trace=ctx.trace,
        )

    # ─── Utility ──────────────────────────────────────────────────────────────

    @staticmethod
    def _emit(ctx: _RunContext, event_type: str, data) -> None:
        """Emit an event to the bus (no-op if no bus is attached)."""
        if ctx.event_bus:
            ctx.event_bus.emit(event_type, data)
        else:
            logger.debug(f"[{event_type}] {data}")

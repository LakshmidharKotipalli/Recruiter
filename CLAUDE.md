# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server (hot-reload)
uvicorn main:app --reload --port 8000

# Quick smoke test (mock mode, no API keys needed)
python3 -c "
from pipeline import AgenticPipeline
from schemas.models import PipelineInput
import asyncio
r = asyncio.run(AgenticPipeline().run(PipelineInput(raw_jd='Senior Python Engineer FastAPI Bangalore')))
print(f'accepted={len(r.accepted)} trace_steps={len(r.execution_trace.steps)} hitl={r.execution_trace.hitl_decisions}')
"

# Test SSE stream endpoint
curl -N -X POST http://localhost:8000/api/run/stream \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore" | head -30

# Resume a HITL checkpoint (replace RUN_ID from SSE X-Run-Id header)
curl -s -X POST http://localhost:8000/api/run/RUN_ID/resume \
  -H "Content-Type: application/json" \
  -d '{"checkpoint":"jd_review","spec_overrides":{},"decision":"proceed"}'

# Non-streaming endpoint (uses same AgenticPipeline internally)
curl -s -X POST http://localhost:8000/api/run \
  -F "jd_text=Senior Python Engineer" | python3 -m json.tool | head -10

# Resume matching (non-streaming)
curl -s -X POST http://localhost:8000/api/resume/match \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore" \
  -F "resumes=@resume1.pdf" | python3 -m json.tool | head -10

# Resume matching (SSE streaming)
curl -N -X POST http://localhost:8000/api/resume/match/stream \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore" \
  -F "resumes=@resume1.pdf" | head -30
```

**Environment:** Copy `.env.example` to `.env`. Set `MOCK_MODE=false` and `ANTHROPIC_API_KEY=sk-...` to run against real APIs. Without changes, everything runs in mock mode.

## Application Architecture

Seven discrete pipeline layers, orchestrated by `AgenticPipeline`. Data flows downward through typed Pydantic schemas defined in `schemas/models.py`.

```
Layer 01  Input               PipelineInput: raw_jd + RecruiterConfig
Layer 02  Intelligence        JDExtractionAgent — 3-step clean/enrich/extract → SearchSpec
Layer 03  Query Generation    QueryGenerationAgent — Boolean queries per source → QuerySet
Layer 04  External Integration ProfileFetchingAgent — async parallel dispatch → RawProfile[]
Layer 05  Data Processing     DeduplicationStage — P1–P4 identity resolution → DedupedProfile[]
Layer 06  Scoring             RankingAgent — weighted score + NL reasoning → ScoredProfile[]
Layer 07  Output              PipelineOutput: accepted[], close[], rejected_count, execution_trace
```

### API Endpoints

| Endpoint | Method | Streaming | Description |
|----------|--------|-----------|-------------|
| `/` | GET | — | Serve frontend UI (static/index.html) |
| `/api/config` | GET | — | Return model + source config (keys redacted) |
| `/api/run` | POST | No | Run sourcing pipeline, return JSON |
| `/api/run/stream` | POST | SSE | Run sourcing pipeline with real-time events |
| `/api/run/{run_id}/resume` | POST | — | Resume a HITL checkpoint |
| `/api/resume/match` | POST | No | Match uploaded resumes against JD |
| `/api/resume/match/stream` | POST | SSE | Resume matching with real-time events |

### Pipeline Classes in `pipeline.py`

| Class | Description |
|-------|-------------|
| `AgenticPipeline` | Primary pipeline. Creates a fresh `OrchestratorAgent` per request. Supports SSE events, HITL checkpoints, and execution trace. Used by both `/api/run` and `/api/run/stream`. |
| `ResumeMatchPipeline` | Resume upload flow. JD extraction → resume parsing → dedup → ranking. Skips query generation and external fetching. |

### Concurrency Model

`AgenticPipeline._build_orchestrator()` creates a **new `OrchestratorAgent` per request**. The orchestrator uses a `_RunContext` dataclass to hold all per-run mutable state (search spec, profiles, scores, trace). This ensures concurrent requests cannot interfere with each other.

### Agentic Layer (`agents/orchestrator.py`)

`OrchestratorAgent` drives the pipeline in two modes:

- **Mock mode (`_mock_run`)**: Linear sequence emitting SSE events and auto-resolving HITL checkpoints after 0.5s.
- **Live mode (`_agentic_run`)**: Anthropic `tool_use` ReAct loop. LLM calls tools by name; orchestrator dispatches to existing agents. Loop terminates when `assess_quality` returns `accept`/`force_accept` or after max iterations (configurable, default 3).

The 5 Anthropic tool schemas live in `agents/tools.py`:
`extract_jd` → `generate_queries` → `fetch_profiles` → `score_candidates` → `assess_quality`

`ResumeParserAgent` (in `agents/resume_parser.py`) parses uploaded PDF/text resumes into structured `RawProfile` objects using LLM extraction with mock fallback. Used by `ResumeMatchPipeline` to bypass query generation and external fetching.

`QualityChecker.assess()` (in `agents/quality_checker.py`) provides the decision logic — pure Python, no LLM involved. Decision rules:

- At max iterations → `accept` (or `force_accept` if 0 accepted)
- `accepted >= min_accepted` → `accept`
- `total_raw < min_raw_per_iter * iteration` → `refine_broader`
- `close > accepted * 3` AND sufficient pool → `refine_location_relaxed`
- else → `refine_broader`

All thresholds are configurable via env vars (see `.env.example`).

### Human-in-the-Loop (HITL)

Two mandatory checkpoints pause the pipeline for recruiter review:

**HITL-1 — after JD extraction**: Emits `hitl_jd_review` SSE event with the extracted `SearchSpec`. Recruiter approves via `POST /api/run/{run_id}/resume` with `{"checkpoint":"jd_review","spec_overrides":{...}}`. Overrides are **validated against the SearchSpec model** before applying.

**HITL-2 — after profile fetching if raw count < threshold**: Emits `hitl_profile_count` SSE event. Options: `broaden`, `relax_location`, `proceed`.

HITL timeout (default 5 min) auto-proceeds with defaults. Mock mode skips real waits.

### SSE Event Flow

```
POST /api/run/stream
  → creates run_id, per-request EventBus (with backpressure), per-process HITLStore singleton
  → spawns AgenticPipeline.run() as asyncio.Task
  → returns StreamingResponse(event_bus.stream())

Events emitted: pipeline_start → agent_start → agent_complete → tool_call →
                hitl_jd_review → hitl_resumed →
                hitl_profile_count → hitl_resumed →
                quality_assessed → [refinement →] pipeline_complete
```

### Data Schema Flow

```
PipelineInput(raw_jd, RecruiterConfig)
  → SearchSpec { role_title, core_requirements, must_have_skills[], nice_to_have_skills[],
                 inferred_skills[], seniority_level, location, weights{},
                 jd_quality_score(0-10), jd_issues[], cleaned_jd, gap_flags[] }
  → QuerySet { google_cse, naukri, pdl }  (each: primary[], fallback[])
  → RawProfile[] { name, title, company, skills[], experience[], source, profile_url, ... }
  → DedupedProfile[] { ...merged, alt_profiles[{source, url, field_diffs}] }
  → ScoredProfile[] { score_pct, matched[], gaps[], reasoning, bucket: accepted|close|rejected }
  → PipelineOutput { accepted[], close[], rejected_count, search_spec, execution_trace }
```

### Scoring

```
score = w₁×must_have_pct + w₂×nice_to_have_pct + w₃×seniority_proximity + w₄×location_match
```
Default weights: `0.50 / 0.25 / 0.15 / 0.10`. **Must-have gate:** missing >1 must-have skill → auto-rejected regardless of score.

### JD Extraction (Layer 02 — 2 sequential LLM calls)

| Step | What it does |
|------|-------------|
| 1 — JD Cleaner | Scores quality 0–10, lists issues, rewrites into clean prose |
| 2 — Gap Filler + Extractor | Applies role knowledge, infers skills, produces final typed `SearchSpec` |

Mock detection heuristic: `len(raw_jd) < 120 or newline_count < 2` → selects a role-type-specific mock response.

### Frontend

Single-page app served from `static/index.html`:

- **Two workflows**: Candidate Sourcing (external profile search) and Resume Matching (upload resumes against JD)
- **Real-time SSE event streaming** with progress indicators for each pipeline stage
- **HITL checkpoint UI** for recruiter review — inline approval/override of JD extraction and profile count decisions
- **Weight sliders and threshold controls** for tuning scoring parameters per search

## File Structure

```
Recruiter/
├── main.py                    # FastAPI app, route definitions, SSE endpoints
├── pipeline.py                # AgenticPipeline + ResumeMatchPipeline
├── config.py                  # PipelineConfig, AppConfig (env-driven)
├── agents/
│   ├── base.py                # BaseAgent — LLM call abstraction
│   ├── orchestrator.py        # OrchestratorAgent — ReAct loop + mock mode
│   ├── jd_extraction.py       # JDExtractionAgent — clean/enrich/extract
│   ├── query_generation.py    # QueryGenerationAgent — Boolean queries
│   ├── profile_fetching.py    # ProfileFetchingAgent — async parallel fetch
│   ├── resume_parser.py       # ResumeParserAgent — PDF/text → RawProfile
│   ├── deduplication.py       # DeduplicationStage — P1–P4 identity resolution
│   ├── ranking.py             # RankingAgent — weighted scoring
│   ├── quality_checker.py     # QualityChecker — pure Python decision logic
│   ├── tools.py               # Anthropic tool schemas for agentic loop
│   ├── event_bus.py           # Per-request EventBus with backpressure
│   └── hitl.py                # HITLStore — in-memory checkpoint state
├── integrations/
│   ├── base.py                # BaseIntegration interface
│   ├── google_cse.py          # Google Custom Search Engine
│   ├── naukri.py              # Naukri scraper
│   └── pdl.py                 # People Data Labs API
├── schemas/
│   └── models.py              # All Pydantic models (SearchSpec, RawProfile, etc.)
├── static/
│   └── index.html             # Frontend SPA
├── requirements.txt
├── .env.example
└── architecture.html          # Visual architecture diagram
```

## Security

- **API Key auth**: Set `APP_API_KEY` in `.env` to require `X-API-Key` header on all mutation endpoints. Leave empty to disable.
- **CORS**: Configurable origins via `CORS_ORIGINS` env var (comma-separated). Defaults to `localhost:3000,localhost:8000`.
- **Rate limiting**: In-memory per-IP rate limiter. Configurable via `RATE_LIMIT_RPM` (default 30).
- **Input validation**: Form weights are clamped to [0,1], thresholds are normalized, HITL spec overrides are validated against the SearchSpec Pydantic model before applying.

## Configuration

All magic numbers are configurable via environment variables. See `.env.example` for the full list with defaults. Key config is centralized in `config.py`:

- `PipelineConfig` — HITL thresholds, iteration limits, quality checker params
- `AppConfig` — CORS, auth, rate limiting, source API keys

## Key Design Decisions

- **Per-request orchestrator** — `AgenticPipeline` creates a fresh `OrchestratorAgent` per run. State is isolated in a `_RunContext` dataclass. No shared mutable state between concurrent requests.
- **Per-request EventBus with backpressure** — max queue size prevents unbounded memory growth. Created fresh per SSE request, discarded on completion.
- **`_call_llm_with_tools()` is Anthropic-only** — raises `ValueError` if `provider != "anthropic"`. Plain `_call_llm()` in `BaseAgent` supports both Anthropic and OpenAI.
- **`HITLStore` is a single-process in-memory dict** — adequate for single-instance. Replace with Redis for multi-instance deployments.
- **mock_mode=True default** — `MOCK_MODE` env var controls this. All integrations and LLM agents return deterministic hardcoded data when true.
- **Robust JSON parsing** — `BaseAgent._parse_json_response()` uses balanced-bracket extraction as fallback instead of fragile `min()`/`rfind()` heuristics.

## POC Scope (Phase 1)

Three sources active: **Google CSE**, **Naukri**, **People Data Labs**. Additional sources slot in as pluggable modules in `integrations/`. The architecture diagram (open `architecture.html` in a browser) is the visual source of truth for the layer design.

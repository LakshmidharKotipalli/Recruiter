# AI Recruiter

An AI-powered recruiting pipeline that understands job descriptions, finds candidates across multiple sources, and delivers a scored, ranked shortlist — all in real time.

## Two Operating Modes

**Candidate Sourcing** — Paste a job description. The AI searches Google CSE, Naukri, and People Data Labs in parallel, deduplicates results, scores every candidate against the JD, and returns a ranked shortlist with natural-language reasoning.

**Resume Matching** — Paste a job description and upload resume files (PDF or plain text). The AI parses each resume, scores it against JD requirements, and ranks them side by side.

Both modes stream progress to the browser via Server-Sent Events and pause at human-in-the-loop checkpoints so the recruiter stays in control.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your environment file (runs in mock mode by default — no API keys needed)
cp .env.example .env

# 3. Start the server
uvicorn main:app --reload --port 8000

# 4. Open the UI
open http://localhost:8000
```

To connect to real sources, set `MOCK_MODE=false` in `.env` and provide the relevant API keys (see [Configuration](#configuration)).

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| LLM Orchestration | Anthropic Claude API (`tool_use` ReAct loop) |
| Data Validation | Pydantic v2 |
| Real-time Streaming | Server-Sent Events (SSE) |
| Frontend | Vanilla HTML / CSS / JS (single-page app) |
| PDF Extraction | pypdf |

---

## Architecture

Seven discrete pipeline layers, each producing typed Pydantic schemas consumed by the next.

| Layer | Name | What It Does |
|:-----:|------|-------------|
| 1 | **Input** | Raw JD text + recruiter configuration (weights, thresholds) |
| 2 | **Intelligence** | JD extraction — clean, enrich, extract into a typed `SearchSpec` |
| 3 | **Query Generation** | Boolean queries per source (sourcing mode only) |
| 4 | **Profile Acquisition** | Async parallel fetch from 3 sources, or resume parsing |
| 5 | **Deduplication** | P1-P4 identity resolution across sources |
| 6 | **Scoring & Ranking** | Weighted formula + must-have gate + NL reasoning |
| 7 | **Output** | Bucketed results (accepted / close / rejected) + execution trace |

### Agentic Orchestration

`OrchestratorAgent` drives the pipeline in two modes:

- **Mock mode** — Deterministic state machine. No LLM calls, no API keys required. Ideal for development and demos.
- **Live mode** — Anthropic `tool_use` ReAct loop. The LLM decides tool invocation order; the orchestrator dispatches to specialized agents. The loop terminates when `QualityChecker` returns `accept` / `force_accept` or after max iterations (default 3).

Each request gets a fresh orchestrator with isolated state (`_RunContext` dataclass), so concurrent requests never interfere.

### Human-in-the-Loop (HITL) Checkpoints

| Checkpoint | Trigger | Recruiter Action |
|------------|---------|-----------------|
| **HITL-1 — JD Review** | After JD extraction | Review and optionally edit the extracted SearchSpec (skills, title, location, seniority) |
| **HITL-2 — Profile Count** | After fetching, if the candidate pool is thin | Broaden search, relax location, or proceed as-is |

Both checkpoints auto-proceed after a configurable timeout (default 5 minutes).

### Scoring Formula

```
score = 0.50 x must_have_pct
      + 0.25 x nice_to_have_pct
      + 0.15 x seniority_proximity
      + 0.10 x location_match
```

Weights are adjustable via the UI. **Must-have gate:** missing more than 1 must-have skill results in automatic rejection regardless of overall score.

---

## API Endpoints

| Endpoint | Method | Streaming | Description |
|----------|--------|:---------:|-------------|
| `/` | GET | — | Serve the frontend UI |
| `/api/config` | GET | — | Return model and source configuration |
| `/api/run` | POST | No | Run the sourcing pipeline (JSON response) |
| `/api/run/stream` | POST | SSE | Run the sourcing pipeline with real-time events |
| `/api/run/{run_id}/resume` | POST | — | Resume a HITL checkpoint |
| `/api/resume/match` | POST | No | Match uploaded resumes against a JD (JSON) |
| `/api/resume/match/stream` | POST | SSE | Match uploaded resumes against a JD (SSE) |

### Example: curl

```bash
# Sourcing (SSE streaming)
curl -N -X POST http://localhost:8000/api/run/stream \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore"

# Resume matching
curl -s -X POST http://localhost:8000/api/resume/match \
  -F "jd_text=Senior Python Engineer" \
  -F "resumes=@resume1.pdf" | python3 -m json.tool

# Resume a HITL checkpoint (replace RUN_ID with the value from the SSE X-Run-Id header)
curl -s -X POST http://localhost:8000/api/run/RUN_ID/resume \
  -H "Content-Type: application/json" \
  -d '{"checkpoint":"jd_review","spec_overrides":{},"decision":"proceed"}'
```

---

## Configuration

Copy `.env.example` to `.env`. All settings have sensible defaults.

| Variable | Purpose | Default |
|----------|---------|---------|
| `MOCK_MODE` | Deterministic test data, no API keys needed | `true` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key (required for live mode) | — |
| `GOOGLE_CSE_KEY` | Google Custom Search API key | — |
| `GOOGLE_CSE_CX` | Google Custom Search engine ID | — |
| `NAUKRI_KEY` | Naukri.com API key | — |
| `PDL_KEY` | People Data Labs API key | — |
| `APP_API_KEY` | API key auth — if set, all mutation endpoints require `X-API-Key` header | — |
| `CORS_ORIGINS` | Comma-separated allowed origins | `localhost:3000,localhost:8000` |
| `RATE_LIMIT_RPM` | Requests per minute per IP | `30` |
| `MAX_ITERATIONS` | Maximum agentic loop iterations | `3` |
| `HITL_TIMEOUT_SECONDS` | Auto-proceed timeout for HITL checkpoints | `300` |

See `.env.example` for the full list with descriptions.

### Security

- **API key auth** — Set `APP_API_KEY` to require an `X-API-Key` header on all mutation endpoints.
- **CORS** — Configurable origins via `CORS_ORIGINS`.
- **Rate limiting** — In-memory per-IP limiter, configurable via `RATE_LIMIT_RPM`.
- **Input validation** — Weights clamped to [0, 1], HITL overrides validated against the SearchSpec Pydantic model.

---

## Project Structure

```
Recruiter/
├── main.py                    # FastAPI server, route definitions, SSE endpoints
├── pipeline.py                # AgenticPipeline + ResumeMatchPipeline
├── config.py                  # Centralized configuration (env-driven)
├── agents/
│   ├── base.py                # BaseAgent — LLM call abstraction
│   ├── orchestrator.py        # OrchestratorAgent — ReAct loop + mock mode
│   ├── jd_extraction.py       # JD cleaning, enrichment, extraction
│   ├── query_generation.py    # Boolean query generation per source
│   ├── profile_fetching.py    # Async parallel profile dispatch
│   ├── resume_parser.py       # Resume text → structured profile
│   ├── deduplication.py       # P1–P4 identity resolution
│   ├── ranking.py             # Scoring, bucketing, NL reasoning
│   ├── quality_checker.py     # Pure-Python quality assessment logic
│   ├── hitl.py                # HITL checkpoint management
│   ├── event_bus.py           # Per-request SSE event streaming
│   └── tools.py               # Anthropic tool_use schemas
├── integrations/
│   ├── base.py                # BaseIntegration abstract class
│   ├── google_cse.py          # Google Custom Search Engine
│   ├── naukri.py              # Naukri.com API
│   └── pdl.py                 # People Data Labs
├── schemas/
│   └── models.py              # All Pydantic data models
├── static/
│   └── index.html             # Frontend single-page app
├── .env.example               # Environment variable template
├── requirements.txt           # Python dependencies
├── architecture.html          # Visual architecture diagram
├── claude-agents-explainer.html  # Claude Code sub-agent documentation
├── python-agents-explainer.html  # Python agent module documentation
└── CLAUDE.md                  # Claude Code project guidance
```

### Agent Systems

This project contains two complementary agent systems:

1. **`agents/`** — 12 Python modules powering the FastAPI backend pipeline (orchestrator, JD extraction, ranking, etc.)
2. **`.claude/agents/`** — 6 Claude Code sub-agents providing a CLI-based pipeline workflow

See the HTML documentation files linked below for detailed explanations.

---

## Documentation

Open these files in a browser for interactive visual documentation:

- **[architecture.html](architecture.html)** — System architecture diagram showing all 7 pipeline layers and data flow
- **[claude-agents-explainer.html](claude-agents-explainer.html)** — Claude Code sub-agent roles and interaction patterns
- **[python-agents-explainer.html](python-agents-explainer.html)** — Python agent module responsibilities and orchestration flow

---

## Current Status

**Phase 1 POC — Complete.**

- All 7 pipeline layers implemented and operational.
- Agentic orchestration working in both mock and live Anthropic modes.
- HITL checkpoints functional with configurable auto-proceed timeout.
- SSE real-time streaming operational.
- Frontend UI complete with dual-mode workflow (sourcing + resume matching).
- Three active sources: **Google CSE**, **Naukri**, **People Data Labs**. Additional sources slot in as pluggable modules under `integrations/`.

---

## License

Proprietary.

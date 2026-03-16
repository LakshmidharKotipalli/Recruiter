# AI Recruiter

An AI-powered recruiting pipeline that understands job descriptions, finds or evaluates candidates, and delivers a ranked shortlist.

Two operating modes:

- **Sourcing Mode** -- Paste a job description. The AI searches Google CSE, Naukri, and People Data Labs simultaneously, deduplicates results, scores candidates, and returns a ranked shortlist.
- **Resume Match Mode** -- Paste a job description and upload resume files. The AI parses each resume, scores everyone against the JD requirements, and ranks them.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running](#running)
- [API Endpoints](#api-endpoints)
- [Usage Guide](#usage-guide)
- [Scoring Formula](#scoring-formula)
- [Project Structure](#project-structure)
- [Current Status](#current-status)
- [License](#license)

---

## Tech Stack

- Python 3.10+, FastAPI, Uvicorn
- Anthropic Claude API (tool_use ReAct loop for agentic orchestration)
- Pydantic v2 for typed data schemas
- Server-Sent Events (SSE) for real-time streaming
- Single-page frontend (vanilla HTML/CSS/JS)
- pypdf for PDF text extraction

---

## Architecture

The application is organized as a seven-layer pipeline, orchestrated by `AgenticPipeline`. Data flows downward through typed Pydantic schemas.

| Layer | Name | Description |
|-------|------|-------------|
| 1 | Input | Raw JD text + recruiter configuration |
| 2 | Intelligence | JD extraction: clean, enrich, extract into a typed `SearchSpec` |
| 3 | Query Generation | Boolean queries per source (sourcing mode only) |
| 4 | Profile Acquisition | Async parallel fetch from 3 sources, or resume parsing |
| 5 | Deduplication | P1-P4 identity resolution across sources |
| 6 | Scoring and Ranking | Weighted formula + must-have gate + natural-language reasoning |
| 7 | Output | Bucketed results (accepted / close / rejected) + execution trace |

A visual architecture diagram is available by opening `architecture.html` in a browser.

---

## Installation

```bash
pip install -r requirements.txt
```

Dependencies: fastapi, uvicorn, anthropic, pydantic, python-multipart, aiofiles, python-dotenv, pypdf.

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Key environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `MOCK_MODE` | Run with deterministic test data (no API keys needed) | `true` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key (required for live mode) | -- |
| `GOOGLE_CSE_KEY` | Google Custom Search API key | -- |
| `GOOGLE_CSE_CX` | Google Custom Search engine ID | -- |
| `NAUKRI_KEY` | Naukri.com API key | -- |
| `PDL_KEY` | People Data Labs API key | -- |
| `APP_API_KEY` | Optional API key auth (`X-API-Key` header required if set) | -- |
| `CORS_ORIGINS` | Comma-separated allowed origins | `localhost:3000,localhost:8000` |
| `RATE_LIMIT_RPM` | Requests per minute per IP | `30` |
| `HITL_MIN_RAW` | Minimum raw profiles before HITL-2 triggers | -- |
| `MIN_ACCEPTED` | Minimum accepted candidates for quality check | -- |
| `MAX_ITERATIONS` | Maximum agentic loop iterations | `3` |
| `HITL_TIMEOUT_SECONDS` | Auto-proceed timeout for HITL checkpoints | `300` |

To run in live mode, set `MOCK_MODE=false` and provide the relevant API keys.

---

## Running

```bash
# Start the server with hot reload
uvicorn main:app --reload --port 8000

# Open the UI in your browser
open http://localhost:8000
```

Quick smoke test (mock mode, no API keys needed):

```bash
python3 -c "
from pipeline import AgenticPipeline
from schemas.models import PipelineInput
import asyncio
r = asyncio.run(AgenticPipeline().run(PipelineInput(raw_jd='Senior Python Engineer FastAPI Bangalore')))
print(f'accepted={len(r.accepted)} trace_steps={len(r.execution_trace.steps)} hitl={r.execution_trace.hitl_decisions}')
"
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve the frontend UI |
| `/api/config` | GET | Return model and source configuration |
| `/api/run` | POST | Run the sourcing pipeline (JSON response) |
| `/api/run/stream` | POST | Run the sourcing pipeline (SSE streaming) |
| `/api/run/{run_id}/resume` | POST | Resume a HITL checkpoint |
| `/api/resume/match` | POST | Match uploaded resumes against a JD (JSON response) |
| `/api/resume/match/stream` | POST | Match uploaded resumes against a JD (SSE streaming) |

---

## Usage Guide

### Quick Start (Mock Mode)

1. Start the server: `uvicorn main:app --reload --port 8000`
2. Open http://localhost:8000 in your browser.
3. Paste any job description into the text area.
4. Click **Start Sourcing**.
5. Watch real-time progress via SSE events.
6. Review the HITL checkpoint (the extracted SearchSpec) -- approve or edit fields.
7. View ranked candidates with scores, matched skills, gaps, and reasoning.

### Candidate Sourcing

1. Paste or upload a job description.
2. Optionally adjust the weight sliders (must-have 50%, nice-to-have 25%, seniority 15%, location 10%).
3. Set acceptance thresholds.
4. Click **Start Sourcing**.
5. The pipeline runs: JD extraction, HITL review, query generation, parallel fetch, deduplication, scoring.
6. Review results: accepted candidates (green), close matches (gold), and rejected count.

### Resume Matching

1. Switch to the **Resume Match** tab.
2. Paste or upload a job description.
3. Upload one or more resume files (PDF or plain text).
4. Click **Match Resumes**.
5. The pipeline runs: JD extraction, HITL review, resume parsing, deduplication, scoring.
6. Review how each resume scores against the JD requirements.

### HITL (Human-in-the-Loop) Checkpoints

Two mandatory pause points allow recruiter review before the pipeline continues:

- **HITL-1 (JD Review)** -- After JD extraction, the extracted SearchSpec is presented. The recruiter can edit skills, title, location, and seniority before proceeding.
- **HITL-2 (Profile Count)** -- After fetching, if the candidate pool is smaller than the configured threshold. Options: broaden the search, relax location constraints, or proceed as-is.

Both checkpoints auto-proceed after 5 minutes (configurable via `HITL_TIMEOUT_SECONDS`) with default decisions.

### Programmatic Usage (curl)

**Sourcing (non-streaming):**

```bash
curl -s -X POST http://localhost:8000/api/run \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore" | python3 -m json.tool
```

**Sourcing (SSE streaming):**

```bash
curl -N -X POST http://localhost:8000/api/run/stream \
  -F "jd_text=Senior Python Engineer FastAPI Bangalore"
```

**Resume matching:**

```bash
curl -s -X POST http://localhost:8000/api/resume/match \
  -F "jd_text=Senior Python Engineer" \
  -F "resumes=@resume1.pdf"
```

**Resuming a HITL checkpoint** (replace `RUN_ID` with the value from the SSE `X-Run-Id` header):

```bash
curl -s -X POST http://localhost:8000/api/run/RUN_ID/resume \
  -H "Content-Type: application/json" \
  -d '{"checkpoint":"jd_review","spec_overrides":{},"decision":"proceed"}'
```

---

## Scoring Formula

```
score = w1 * must_have_pct + w2 * nice_to_have_pct + w3 * seniority_proximity + w4 * location_match
```

Default weights: 50% / 25% / 15% / 10%. These are configurable by the recruiter via the UI sliders.

**Must-have gate:** If a candidate is missing more than 1 must-have skill, they are auto-rejected regardless of their overall score.

**Bucketing:**

| Bucket | Criteria |
|--------|----------|
| Accepted | Above threshold with all must-have skills present |
| Close Match | High score but missing 1 must-have skill or wrong seniority level |
| Rejected | Missing 2+ must-have skills or extreme mismatch |

---

## Project Structure

```
.
├── main.py                  # FastAPI server (7 endpoints, middleware)
├── pipeline.py              # AgenticPipeline & ResumeMatchPipeline
├── config.py                # Centralized configuration (env vars)
├── agents/
│   ├── base.py              # BaseAgent (LLM client, tool_use)
│   ├── orchestrator.py      # OrchestratorAgent (agentic loop)
│   ├── jd_extraction.py     # JD cleaning, enrichment, extraction
│   ├── query_generation.py  # Boolean query generation per source
│   ├── profile_fetching.py  # Async parallel profile dispatch
│   ├── deduplication.py     # P1-P4 identity resolution
│   ├── ranking.py           # Scoring, bucketing, NL reasoning
│   ├── resume_parser.py     # Resume text to structured profile
│   ├── quality_checker.py   # Pure-Python quality assessment
│   ├── hitl.py              # HITL checkpoint management
│   ├── event_bus.py         # SSE event streaming
│   └── tools.py             # Anthropic tool_use schemas
├── integrations/
│   ├── base.py              # BaseIntegration abstract class
│   ├── google_cse.py        # Google Custom Search
│   ├── naukri.py            # Naukri.com API
│   └── pdl.py               # People Data Labs
├── schemas/
│   └── models.py            # All Pydantic data models
├── static/
│   └── index.html           # Frontend single-page app
├── .env.example             # Environment variable template
├── requirements.txt         # Python dependencies
├── architecture.html        # Visual architecture diagram
└── CLAUDE.md                # Claude Code project guidance
```

---

## Current Status

**Phase 1 POC -- Complete.**

- All 7 pipeline layers fully implemented.
- Agentic orchestration working in both mock and live Anthropic modes.
- HITL checkpoints functional with timeout-based auto-proceed.
- SSE streaming operational.
- Frontend UI complete.
- External integrations (Google CSE, Naukri, People Data Labs) are **mock-only** by default. Set `MOCK_MODE=false` and provide the relevant API keys to connect to real sources.

---

## License

Proprietary.

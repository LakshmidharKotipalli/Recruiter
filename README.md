# AI Recruiter

An agentic recruiting pipeline that reads a job description, sources candidates from multiple platforms, and
returns a scored, ranked shortlist with reasoning, streamed live to the browser.

**Two modes**

- **Candidate sourcing:** paste a JD. The agent searches Google CSE, Naukri and People Data Labs in parallel,
  deduplicates profiles, and ranks them against the JD.
- **Resume matching:** paste a JD and upload resumes (PDF or text) to score and rank them side by side.

Both modes pause at human-in-the-loop checkpoints so the recruiter stays in control.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # mock mode by default, no API keys needed
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000. For real sources, set `MOCK_MODE=false` and add `ANTHROPIC_API_KEY` plus the source
keys in `.env`.

## Architecture

Seven pipeline layers, each passing typed Pydantic models to the next:

| Layer | What it does |
|---|---|
| Input | JD text plus recruiter weights and thresholds |
| Intelligence | Cleans and enriches the JD into a typed `SearchSpec` |
| Query generation | Boolean queries per source |
| Profile acquisition | Async parallel fetch, or resume parsing |
| Deduplication | Identity resolution across sources |
| Scoring and ranking | Weighted score, must-have gate, natural-language reasoning |
| Output | Accepted, close and rejected buckets with an execution trace |

`OrchestratorAgent` runs the pipeline as a Claude `tool_use` ReAct loop, refining the search until a pure-Python
quality checker accepts the results (max 3 iterations). Mock mode swaps in a deterministic state machine.
Each request gets its own orchestrator, so concurrent runs never share state.

**Human-in-the-loop checkpoints**

1. **JD review:** edit the extracted skills, title, location and seniority before searching.
2. **Thin pool:** if too few profiles come back, broaden, relax location, or proceed.

Both auto-proceed after a timeout (default 5 minutes).

**Scoring**

```
score = 0.50 × must_have + 0.25 × nice_to_have + 0.15 × seniority + 0.10 × location
```

Weights are adjustable in the UI. Missing more than one must-have skill auto-rejects a candidate.

See [architecture.html](architecture.html) and [subagent-architecture.html](subagent-architecture.html) for
visual diagrams.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/run` | POST | Run sourcing (JSON) |
| `/api/run/stream` | POST | Run sourcing with SSE events |
| `/api/run/{run_id}/resume` | POST | Resume a HITL checkpoint |
| `/api/resume/match` | POST | Match uploaded resumes (JSON) |
| `/api/resume/match/stream` | POST | Match uploaded resumes with SSE events |

```bash
curl -N -X POST http://localhost:8000/api/run/stream -F "jd_text=Senior Python Engineer FastAPI Bangalore"
```

## Configuration

Key `.env` settings (full list in `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `MOCK_MODE` | Deterministic data, no API keys | `true` |
| `ANTHROPIC_API_KEY` | Required for live mode | |
| `GOOGLE_CSE_KEY`, `GOOGLE_CSE_CX`, `NAUKRI_KEY`, `PDL_KEY` | Source credentials | |
| `APP_API_KEY` | Require `X-API-Key` on mutation endpoints | |
| `RATE_LIMIT_RPM` | Per-IP rate limit | `30` |

## Tech stack

FastAPI, Anthropic Claude (`tool_use`), Pydantic v2, Server-Sent Events, vanilla HTML/JS frontend, pypdf.

## Project structure

```
main.py          FastAPI routes and SSE endpoints
pipeline.py      AgenticPipeline and ResumeMatchPipeline
agents/          Orchestrator, JD extraction, query generation, fetching, dedup, ranking, HITL
integrations/    Google CSE, Naukri, People Data Labs
schemas/         Pydantic models
static/          Single-page frontend
.claude/agents/  Claude Code sub-agents for a CLI version of the pipeline
```

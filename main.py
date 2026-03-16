import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, Form, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional

from config import APP_CONFIG
from schemas.models import (
    HITLDecision,
    PipelineInput,
    PipelineOutput,
    RecruiterConfig,
    WeightConfig,
    ThresholdConfig,
    HardRejectRules,
)
from pipeline import AgenticPipeline, ResumeMatchPipeline
from agents.event_bus import EventBus
from agents.hitl import get_hitl_store

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Candidate Sourcing Pipeline",
    description="Multi-agent pipeline that sources, deduplicates, and ranks candidates from multiple talent pools.",
    version="2.0.0",
)

# --- CORS middleware (configurable origins) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=APP_CONFIG.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


# --- API Key auth middleware ---
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header when APP_API_KEY is configured."""

    EXEMPT_PATHS = {"/", "/docs", "/openapi.json", "/redoc", "/api/config"}

    async def dispatch(self, request: Request, call_next):
        api_key = APP_CONFIG.api_key
        if not api_key:
            # No key configured — auth disabled
            return await call_next(request)

        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Static files don't need auth
        if request.url.path.startswith("/static"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)


# --- Rate limiting middleware ---
class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter."""

    def __init__(self, app, rpm: int = 30):
        super().__init__(app)
        self.rpm = rpm
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if self.rpm <= 0:
            return await call_next(request)

        # Only rate-limit mutation endpoints
        if request.method != "POST":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = now - 60

        # Prune old entries
        self._hits[client_ip] = [t for t in self._hits[client_ip] if t > window]

        if len(self._hits[client_ip]) >= self.rpm:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {self.rpm} requests/minute"},
            )

        self._hits[client_ip].append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware, rpm=APP_CONFIG.rate_limit_rpm)


# Mount static files directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Pipeline factory (fresh per request) ---
def _get_pipeline() -> AgenticPipeline:
    """Return a new pipeline instance. Orchestrator is created per-run internally."""
    return AgenticPipeline(APP_CONFIG)


def _get_resume_pipeline() -> ResumeMatchPipeline:
    """Return a new resume pipeline. Orchestrator is created per-run internally."""
    return ResumeMatchPipeline(APP_CONFIG)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the single-page UI."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="index.html not found in static/")
    return FileResponse(index_path, media_type="text/html")


@app.get("/api/config")
async def get_config():
    """Return current model + source configuration (without API keys)."""
    cfg = APP_CONFIG
    return {
        "provider": cfg.model.provider,
        "model_id": cfg.model.model_id,
        "max_tokens": cfg.model.max_tokens,
        "mock_mode": cfg.mock_mode,
        "sources": {
            name: {"enabled": src.enabled, "has_key": src.api_key != "mock-key"}
            for name, src in cfg.sources.items()
        },
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MAX_JD_LENGTH = 50_000       # max characters for JD text
MAX_UPLOAD_BYTES = 10_485_760  # 10 MB max file upload


async def _resolve_jd(
    jd_text: Optional[str],
    jd_file: Optional[UploadFile],
) -> str:
    """Extract raw JD text from form input (text paste or file upload)."""
    raw_jd = ""
    if jd_file is not None:
        content = await jd_file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Uploaded file exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
            )
        filename = jd_file.filename or ""
        if filename.lower().endswith(".pdf"):
            raw_jd = _extract_text_from_pdf(content)
        else:
            raw_jd = content.decode("utf-8", errors="replace")
    if not raw_jd and jd_text:
        raw_jd = jd_text.strip()

    if len(raw_jd) > MAX_JD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"JD text exceeds {MAX_JD_LENGTH} character limit.",
        )

    return raw_jd


def _build_recruiter_config(
    w_must_have: float,
    w_nice_to_have: float,
    w_seniority: float,
    w_location: float,
    threshold_accepted: float,
    threshold_close: float,
) -> RecruiterConfig:
    """Build a RecruiterConfig from raw form values with validation."""
    # Clamp weights to [0, 1]
    w_must_have = max(0.0, min(1.0, w_must_have))
    w_nice_to_have = max(0.0, min(1.0, w_nice_to_have))
    w_seniority = max(0.0, min(1.0, w_seniority))
    w_location = max(0.0, min(1.0, w_location))

    # Convert percentage thresholds to 0-1 range
    if threshold_accepted > 1.0:
        threshold_accepted = threshold_accepted / 100.0
    if threshold_close > 1.0:
        threshold_close = threshold_close / 100.0

    threshold_accepted = max(0.0, min(1.0, threshold_accepted))
    threshold_close = max(0.0, min(1.0, threshold_close))

    return RecruiterConfig(
        weights=WeightConfig(
            must_have_skills=w_must_have,
            nice_to_have_skills=w_nice_to_have,
            seniority_level=w_seniority,
            location_match=w_location,
        ),
        hard_reject_rules=HardRejectRules(),
        thresholds=ThresholdConfig(
            accepted_min_score=threshold_accepted,
            close_min_score=threshold_close,
        ),
    )


# ---------------------------------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------------------------------

@app.post("/api/run", response_model=PipelineOutput)
async def run_pipeline(
    jd_text: Optional[str] = Form(None),
    jd_file: Optional[UploadFile] = File(None),
    w_must_have: float = Form(0.50),
    w_nice_to_have: float = Form(0.25),
    w_seniority: float = Form(0.15),
    w_location: float = Form(0.10),
    threshold_accepted: float = Form(70.0),
    threshold_close: float = Form(50.0),
):
    """Run the full pipeline (non-streaming). Returns PipelineOutput JSON."""
    raw_jd = await _resolve_jd(jd_text, jd_file)
    if not raw_jd:
        raise HTTPException(status_code=422, detail="Provide either jd_text or jd_file.")

    recruiter_config = _build_recruiter_config(
        w_must_have, w_nice_to_have, w_seniority, w_location,
        threshold_accepted, threshold_close,
    )
    pipeline_input = PipelineInput(raw_jd=raw_jd, config=recruiter_config)

    logger.info(
        f"POST /api/run — JD length: {len(raw_jd)} chars, "
        f"weights: must={w_must_have} nice={w_nice_to_have} sen={w_seniority} loc={w_location}"
    )
    try:
        pipeline = _get_pipeline()
        result = await pipeline.run(pipeline_input)
        return result
    except Exception as e:
        logger.exception(f"Pipeline execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


@app.post("/api/run/stream")
async def run_pipeline_stream(
    jd_text: Optional[str] = Form(None),
    jd_file: Optional[UploadFile] = File(None),
    w_must_have: float = Form(0.50),
    w_nice_to_have: float = Form(0.25),
    w_seniority: float = Form(0.15),
    w_location: float = Form(0.10),
    threshold_accepted: float = Form(70.0),
    threshold_close: float = Form(50.0),
):
    """Run the agentic pipeline with SSE streaming events."""
    raw_jd = await _resolve_jd(jd_text, jd_file)
    if not raw_jd:
        raise HTTPException(status_code=422, detail="Provide either jd_text or jd_file.")

    recruiter_config = _build_recruiter_config(
        w_must_have, w_nice_to_have, w_seniority, w_location,
        threshold_accepted, threshold_close,
    )
    pipeline_input = PipelineInput(raw_jd=raw_jd, config=recruiter_config)
    run_id = str(uuid.uuid4())
    event_bus = EventBus(max_size=APP_CONFIG.pipeline.event_bus_max_size)
    hitl_store = get_hitl_store()

    logger.info(f"POST /api/run/stream run_id={run_id} JD length={len(raw_jd)}")

    async def _run_and_close():
        """Run pipeline in background and close the event bus when done."""
        try:
            pipeline = _get_pipeline()
            await pipeline.run(
                pipeline_input,
                event_bus=event_bus,
                hitl_store=hitl_store,
                run_id=run_id,
            )
        except Exception as e:
            logger.exception(f"Agentic pipeline failed: {e}")
            event_bus.emit("pipeline_error", {"error": str(e)})
        finally:
            event_bus.close()

    asyncio.create_task(_run_and_close())

    return StreamingResponse(
        event_bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


@app.post("/api/run/{run_id}/resume")
async def resume_hitl(run_id: str, decision: HITLDecision):
    """Resume a paused pipeline at a HITL checkpoint."""
    hitl_store = get_hitl_store()

    key = run_id if decision.checkpoint == "jd_review" else f"{run_id}_profile_count"
    resolved = hitl_store.resolve(key, decision.model_dump())

    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No active HITL checkpoint for run_id={run_id} checkpoint={decision.checkpoint}",
        )

    logger.info(f"HITL resume: run_id={run_id} checkpoint={decision.checkpoint}")
    return {"status": "ok", "run_id": run_id, "checkpoint": decision.checkpoint}


# ---------------------------------------------------------------------------
# Resume Upload endpoints
# ---------------------------------------------------------------------------

async def _parse_resume_files(files: list[UploadFile]) -> list[dict]:
    """Read uploaded resume files and return list of {text, filename} dicts."""
    resumes = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Resume file '{f.filename}' exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
            )
        filename = f.filename or "resume.txt"
        if filename.lower().endswith(".pdf"):
            text = _extract_text_from_pdf(content)
        else:
            text = content.decode("utf-8", errors="replace")
        if text.strip():
            resumes.append({"text": text, "filename": filename})
    return resumes


@app.post("/api/resume/match", response_model=PipelineOutput)
async def resume_match(
    jd_text: Optional[str] = Form(None),
    jd_file: Optional[UploadFile] = File(None),
    resumes: list[UploadFile] = File(...),
    w_must_have: float = Form(0.50),
    w_nice_to_have: float = Form(0.25),
    w_seniority: float = Form(0.15),
    w_location: float = Form(0.10),
    threshold_accepted: float = Form(70.0),
    threshold_close: float = Form(50.0),
):
    """Match uploaded resumes against a job description (non-streaming)."""
    raw_jd = await _resolve_jd(jd_text, jd_file)
    if not raw_jd:
        raise HTTPException(status_code=422, detail="Provide either jd_text or jd_file.")
    if not resumes:
        raise HTTPException(status_code=422, detail="Upload at least one resume file.")

    recruiter_config = _build_recruiter_config(
        w_must_have, w_nice_to_have, w_seniority, w_location,
        threshold_accepted, threshold_close,
    )
    pipeline_input = PipelineInput(raw_jd=raw_jd, config=recruiter_config)
    parsed_resumes = await _parse_resume_files(resumes)

    if not parsed_resumes:
        raise HTTPException(status_code=422, detail="No readable resume content found.")

    logger.info(f"POST /api/resume/match — JD length={len(raw_jd)}, resumes={len(parsed_resumes)}")

    try:
        pipeline = _get_resume_pipeline()
        result = await pipeline.run(pipeline_input, parsed_resumes)
        return result
    except Exception as e:
        logger.exception(f"Resume match pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


@app.post("/api/resume/match/stream")
async def resume_match_stream(
    jd_text: Optional[str] = Form(None),
    jd_file: Optional[UploadFile] = File(None),
    resumes: list[UploadFile] = File(...),
    w_must_have: float = Form(0.50),
    w_nice_to_have: float = Form(0.25),
    w_seniority: float = Form(0.15),
    w_location: float = Form(0.10),
    threshold_accepted: float = Form(70.0),
    threshold_close: float = Form(50.0),
):
    """Match uploaded resumes against a JD with SSE streaming events."""
    raw_jd = await _resolve_jd(jd_text, jd_file)
    if not raw_jd:
        raise HTTPException(status_code=422, detail="Provide either jd_text or jd_file.")
    if not resumes:
        raise HTTPException(status_code=422, detail="Upload at least one resume file.")

    recruiter_config = _build_recruiter_config(
        w_must_have, w_nice_to_have, w_seniority, w_location,
        threshold_accepted, threshold_close,
    )
    pipeline_input = PipelineInput(raw_jd=raw_jd, config=recruiter_config)
    parsed_resumes = await _parse_resume_files(resumes)

    if not parsed_resumes:
        raise HTTPException(status_code=422, detail="No readable resume content found.")

    run_id = str(uuid.uuid4())
    event_bus = EventBus(max_size=APP_CONFIG.pipeline.event_bus_max_size)
    hitl_store = get_hitl_store()

    logger.info(
        f"POST /api/resume/match/stream run_id={run_id} "
        f"JD length={len(raw_jd)} resumes={len(parsed_resumes)}"
    )

    async def _run_and_close():
        try:
            pipeline = _get_resume_pipeline()
            await pipeline.run(
                pipeline_input, parsed_resumes,
                event_bus=event_bus, hitl_store=hitl_store, run_id=run_id,
            )
        except Exception as e:
            logger.exception(f"Resume match pipeline failed: {e}")
            event_bus.emit("pipeline_error", {"error": str(e)})
        finally:
            event_bus.close()

    asyncio.create_task(_run_and_close())

    return StreamingResponse(
        event_bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes using pypdf if available, with regex fallback."""
    # Try pypdf first (reliable extraction)
    try:
        import pypdf
        import io
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        if pages:
            return "\n\n".join(pages)
    except ImportError:
        logger.debug("pypdf not installed; falling back to regex PDF extraction")
    except Exception as e:
        logger.warning(f"pypdf extraction failed: {e}; falling back to regex")

    # Regex fallback for simple unencrypted PDFs
    try:
        import re
        decoded = content.decode("latin-1", errors="replace")
        text_parts = []
        tj_matches = re.findall(r'\(([^)]*)\)\s*Tj', decoded)
        text_parts.extend(tj_matches)
        tj_array = re.findall(r'\[([^\]]*)\]\s*TJ', decoded)
        for arr in tj_array:
            parts = re.findall(r'\(([^)]*)\)', arr)
            text_parts.extend(parts)
        if text_parts:
            return "\n".join(text_parts)
    except Exception:
        pass

    return content.decode("utf-8", errors="replace")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

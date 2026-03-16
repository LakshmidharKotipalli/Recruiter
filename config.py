import os
from pathlib import Path
from pydantic import BaseModel

# Load .env from project root (no-op if file is absent or python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


class ModelConfig(BaseModel):
    provider: str = os.environ.get("LLM_PROVIDER", "anthropic")
    model_id: str = os.environ.get("LLM_MODEL_ID", "claude-sonnet-4-6")
    api_key: str = os.environ.get("ANTHROPIC_API_KEY", "mock-key")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "mock-key")
    max_tokens: int = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

    @property
    def active_api_key(self) -> str:
        """Return the key for the currently configured provider."""
        if self.provider == "openai":
            return self.openai_api_key
        return self.api_key


class SourceConfig(BaseModel):
    api_key: str = "mock-key"
    enabled: bool = True


class PipelineConfig(BaseModel):
    """Tunable pipeline constants — previously hardcoded magic numbers."""
    hitl_min_raw: int = int(os.environ.get("HITL_MIN_RAW", "15"))
    force_accept_top_n: int = int(os.environ.get("FORCE_ACCEPT_TOP_N", "5"))
    min_accepted: int = int(os.environ.get("MIN_ACCEPTED", "5"))
    max_iterations: int = int(os.environ.get("MAX_ITERATIONS", "3"))
    min_raw_per_iter: int = int(os.environ.get("MIN_RAW_PER_ITER", "10"))
    hitl_timeout_seconds: float = float(os.environ.get("HITL_TIMEOUT_SECONDS", "300"))
    name_overlap_threshold: float = float(os.environ.get("NAME_OVERLAP_THRESHOLD", "0.80"))
    event_bus_max_size: int = int(os.environ.get("EVENT_BUS_MAX_SIZE", "1000"))


class AppConfig(BaseModel):
    model: ModelConfig = ModelConfig()
    mock_mode: bool = os.environ.get("MOCK_MODE", "true").lower() == "true"
    pipeline: PipelineConfig = PipelineConfig()
    cors_origins: list[str] = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS", "http://localhost:3000,http://localhost:8000"
        ).split(",")
        if origin.strip()
    ]
    api_key: str = os.environ.get("APP_API_KEY", "")
    rate_limit_rpm: int = int(os.environ.get("RATE_LIMIT_RPM", "30"))
    sources: dict[str, SourceConfig] = {
        "google_cse": SourceConfig(
            api_key=os.environ.get("GOOGLE_CSE_KEY", "mock-key"),
        ),
        "naukri": SourceConfig(
            api_key=os.environ.get("NAUKRI_KEY", "mock-key"),
        ),
        "pdl": SourceConfig(
            api_key=os.environ.get("PDL_KEY", "mock-key"),
        ),
    }


APP_CONFIG = AppConfig()

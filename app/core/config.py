"""
Central application settings, loaded from environment variables / .env.
All tunables that differ between dev / staging / prod live here — nothing
env-specific should be hardcoded elsewhere in the codebase.
"""
from functools import lru_cache
from typing import Optional
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    APP_NAME: str = "P&ID Extraction API"
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = ""

    # --- Database (pluggable) ---
    # One of: mssql | postgres | mysql | mongo | neo4j
    DATABASE_TYPE: str = "mssql"
    DATABASE_URL: str = Field(
        default="mssql+pyodbc://sa:YourStrong!Passw0rd@localhost:1433/pid_extraction"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    )

    # --- MSSQL "simple" config (Option 1) ---
    # Everything actually used by the app goes through DATABASE_URL — these
    # fields exist purely as a friendlier way to fill it in for MSSQL
    # without hand-building a SQLAlchemy URL. See _assemble_mssql_url below,
    # which runs after env loading and overwrites DATABASE_URL from these
    # when DATABASE_TYPE=mssql and MSSQL_ENABLED=true. Leave
    # MSSQL_USERNAME/MSSQL_PASSWORD blank for Windows/trusted authentication
    # (the account running the API process is used).
    MSSQL_ENABLED: bool = False
    MSSQL_SERVER: Optional[str] = None
    MSSQL_DATABASE: Optional[str] = None
    MSSQL_USERNAME: Optional[str] = None
    MSSQL_PASSWORD: Optional[str] = None
    MSSQL_DRIVER: str = "ODBC Driver 18 for SQL Server"
    MSSQL_TRUST_SERVER_CERTIFICATE: bool = True
    # Option 2: a complete SQLAlchemy URL, takes precedence over the
    # MSSQL_SERVER/etc. pieces above if set.
    MSSQL_CONNECTION_STRING: Optional[str] = None

    # Neo4j uses a separate bolt URI + auth pair instead of a SQLAlchemy URL
    NEO4J_URI: Optional[str] = "bolt://localhost:7687"
    NEO4J_USER: Optional[str] = "neo4j"
    NEO4J_PASSWORD: Optional[str] = "password"

    # --- Redis / Celery ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    USE_CELERY: bool = False  # False -> FastAPI BackgroundTasks (simpler dev/demo path)

    # --- Storage ---
    STORAGE_DIR: str = "./storage"
    UPLOAD_DIR: str = "./storage/uploads"
    CROP_DIR: str = "./storage/crops"
    RENDER_DPI: int = 300

    # --- CV symbol detection ---
    YOLO_MODEL_PATH: Optional[str] = "./models_weights/pid_symbols_yolov8.pt"
    CV_CONFIDENCE_DEFAULT: float = 0.75
    CV_DEVICE: str = "cpu"  # "cpu" | "cuda"

    # --- Text extraction ---
    OCR_ENGINE: str = "easyocr"  # "easyocr" | "tesseract"
    VLM_PROVIDER: str = "ollama"  # "none" | "ollama" | "anthropic" | "openai"
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    VLM_MODEL: str = "claude-sonnet-4-6"

    # --- Ollama (local VLM, no API key required) ---
    # Runs against a local/self-hosted Ollama server (`ollama serve`, default
    # port 11434). This is the default VLM_PROVIDER precisely so the vision
    # pipeline works out of the box with zero API keys and zero cloud
    # dependency — just `ollama pull <OLLAMA_VLM_MODEL>` first. Any
    # Ollama-served vision-capable model works — set OLLAMA_VLM_MODEL (or
    # pass ?model=... on any /vlm/* call to override per-request) to
    # whichever you've pulled. Good options as of writing: "qwen3-vl:4b"
    # (the default — small, fast, strong text/OCR accuracy which matters a
    # lot for reading tags off a drawing), "qwen2.5vl", "llama3.2-vision",
    # and "llava" (smallest/most broadly available if you're VRAM-limited).
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_VLM_MODEL: str = "qwen3-vl:4b"
    # A larger num_ctx means a larger KV cache the model has to allocate and
    # run through on every call — noticeably slower than the 4096 default,
    # especially on CPU-only or VRAM-limited setups, and especially on the
    # first request after num_ctx changes (Ollama reloads the model fresh
    # to allocate the new context size). 120s is too tight for that — raised
    # to 300s. If you still see "Read timed out" on a slow machine, raise
    # this further via env rather than lowering OLLAMA_NUM_CTX back down.
    OLLAMA_TIMEOUT_SECONDS: int = 600
    # A full-page P&ID image (as base64) plus the four-table extraction
    # prompt easily runs past Ollama's model-default context window
    # (commonly 4096 tokens), which fails the request with a 400
    # "exceeds the available context size" error rather than truncating
    # silently. Rather than relying on a hand-built Modelfile/custom model
    # tag with a baked-in num_ctx (fragile — forgotten on every re-pull or
    # model swap), num_ctx is sent explicitly on every /api/chat call, so
    # any pulled model gets enough context regardless of its own default.
    # Raise further via env if you still hit context-size errors on dense
    # multi-page drawings. Note this has to cover PROMPT + RESPONSE
    # combined, not just the prompt — a ~6000-token page prompt with only
    # ~6144 total context leaves almost no room for the model to actually
    # write out a multi-table JSON extraction, and the response gets cut
    # off mid-JSON (causing "no valid/parseable JSON" failures that look
    # like a formatting problem but are really a starved context window).
    # 12288 leaves a healthy ~6000 tokens of headroom for the response on
    # top of an observed ~6000-token prompt.
    OLLAMA_NUM_CTX: int = 12288
    # Number of model layers to offload to GPU, passed as `num_gpu` in the
    # request options. Ollama auto-detects this by default — but on hybrid-
    # graphics laptops (an integrated GPU alongside a discrete NVIDIA one)
    # its VRAM estimate can come back overly conservative and it silently
    # falls back to 100% CPU (no error, just ~2-3 tokens/sec instead of the
    # 20-40+ a 4B model should get on a real GPU). Setting this to a large
    # number explicitly asks Ollama to offload as many layers as will
    # actually fit rather than trusting its own estimate. Set to 0 to force
    # pure CPU (e.g. for a fair before/after comparison).
    OLLAMA_NUM_GPU: int = 999
    # Retries a malformed/non-JSON response from the model this many extra
    # times (with an explicit "return valid JSON only" corrective prompt)
    # before giving up and flagging the page for human review instead of
    # failing the whole request.
    OLLAMA_JSON_RETRY_ATTEMPTS: int = 2

    # --- Relationship inference ---
    USE_GNN: bool = False  # rule-based engine runs regardless; GNN is an optional refinement pass
    GNN_MODEL_PATH: Optional[str] = "./models_weights/pid_relations_gnn.pt"

    # --- Human-in-the-loop ---
    AUTO_LEARN_UNKNOWNS_DEFAULT: bool = False

    @model_validator(mode="after")
    def _assemble_mssql_url(self) -> "Settings":
        """
        Builds DATABASE_URL from MSSQL_CONNECTION_STRING or the
        MSSQL_SERVER/MSSQL_DATABASE/... pieces when DATABASE_TYPE=mssql and
        MSSQL_ENABLED=true, so setting those simpler variables actually
        takes effect instead of being silently ignored in favor of
        DATABASE_URL's hardcoded default.
        """
        if self.DATABASE_TYPE != "mssql" or not self.MSSQL_ENABLED:
            return self

        if self.MSSQL_CONNECTION_STRING:
            self.DATABASE_URL = self.MSSQL_CONNECTION_STRING
            return self

        if not self.MSSQL_SERVER or not self.MSSQL_DATABASE:
            return self  # nothing to assemble from — leave DATABASE_URL as-is

        driver_enc = quote_plus(self.MSSQL_DRIVER)
        trust_cert = "yes" if self.MSSQL_TRUST_SERVER_CERTIFICATE else "no"
        query = f"driver={driver_enc}&TrustServerCertificate={trust_cert}"

        if self.MSSQL_USERNAME:
            auth = f"{quote_plus(self.MSSQL_USERNAME)}:{quote_plus(self.MSSQL_PASSWORD or '')}@"
        else:
            # Blank username/password -> Windows/trusted authentication.
            auth = ""
            query += "&trusted_connection=yes"

        self.DATABASE_URL = (
            f"mssql+pyodbc://{auth}{self.MSSQL_SERVER}/{self.MSSQL_DATABASE}?{query}"
        )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


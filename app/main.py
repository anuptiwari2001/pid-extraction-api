import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.logging_config import configure_logging, get_logger
from app.core.errors import register_error_handlers
from app.api.routes import symbols, db_admin, vlm_extraction_db

# duplicate_tag_finder.py lives in utilities/ and is intentionally a
# standalone, dependency-light script (see its own module docstring — it's
# meant to be droppable anywhere, not part of the `app` package). Put
# utilities/ on sys.path so it can still be imported as a bare module here
# without turning it into a package itself.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utilities"))
from duplicate_tag_finder import router as duplicate_tags_router

configure_logging()
logger = get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Trimmed surface: connect/health-check a database (/connect-db, /health), "
        "upload a P&ID PDF for VLM extraction straight into editable, ISA-5.1/PIP-"
        "classified SQL tables with AI-assisted unknown-symbol teaching (/vlm/*), "
        "browse/label unknown symbols and the symbol dictionary (/jobs/*/unknown-"
        "symbols, /label-unknown-symbol, /symbols/dictionary), and find/resolve "
        "duplicate tags (/duplicates/*)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add timeout middleware that logs slow requests but doesn't hard-cut them
# (useful for monitoring which operations are slow, but extraction jobs need
# to complete even if they take 30+ minutes). If you want hard timeouts,
# use an async task queue (Celery) or API gateway with strict timeouts instead.
import asyncio
from time import time
from starlette.middleware.base import BaseHTTPMiddleware


class SlowRequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start_time = time()
        response = await call_next(request)
        process_time = time() - start_time
        # Log requests that take more than 30 seconds
        if process_time > 30:
            logger.warning(
                "slow_request",
                extra={
                    "context": {
                        "method": request.method,
                        "path": request.url.path,
                        "duration_seconds": round(process_time, 2),
                    }
                },
            )
        return response


app.add_middleware(SlowRequestLoggingMiddleware)


register_error_handlers(app)

app.include_router(db_admin.router)
app.include_router(symbols.router)
app.include_router(vlm_extraction_db.router)
if duplicate_tags_router is not None:
    app.include_router(duplicate_tags_router)

# Serve the basic unknown-symbol-labeling demo frontend at /ui
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


@app.on_event("startup")
def on_startup():
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.CROP_DIR, exist_ok=True)
    logger.info("app_started", extra={"context": {"database_type": settings.DATABASE_TYPE, "env": settings.ENV}})


@app.get("/")
def root():
    return {
        "service": settings.APP_NAME,
        "docs": "/docs",
        "health": "/health",
        "unknown_symbol_ui": "/ui",
    }

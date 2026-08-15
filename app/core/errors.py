"""
Custom exception hierarchy + FastAPI exception handlers.
Every error surfaced to the client comes back as a consistent JSON envelope:
    {"error": {"code": "...", "message": "...", "details": {...}}}
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all app-raised errors."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationErrorApp(AppError):
    status_code = 422
    code = "validation_error"


class UnsupportedFileError(AppError):
    status_code = 400
    code = "unsupported_file"


class DatabaseConnectionError(AppError):
    status_code = 503
    code = "database_connection_error"


class ProcessingPausedForUnknownSymbol(AppError):
    """
    Not a "real" error — raised internally to unwind extraction and surface
    the human-in-the-loop payload. Handled specially by the job runner, not
    by the generic HTTP handler below.
    """
    status_code = 202
    code = "awaiting_symbol_label"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logger.error(
            "app_error",
            extra={"context": {"code": exc.code, "path": str(request.url), "details": exc.details}},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "Invalid request", "details": exc.errors()}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logger.exception("unhandled_exception", extra={"context": {"path": str(request.url)}})
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )

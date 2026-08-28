"""Error envelope (R18): every non-2xx body is {"error": {code, message, request_id, details}}."""
from __future__ import annotations

import uuid


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def error_envelope(code: str, message: str, request_id: str | None = None, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or str(uuid.uuid4()),
            "details": details or {},
        }
    }


def install_error_handlers(app) -> None:
    """Attach the envelope to a FastAPI app (import deferred so pure-logic tests don't need fastapi)."""
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    def _rid(request: Request) -> str | None:
        return getattr(getattr(request, "state", None), "request_id", None)

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content=error_envelope(exc.code, exc.message, _rid(request), details=exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=error_envelope("validation_error", "Invalid request.", _rid(request), details={"errors": exc.errors()}))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=error_envelope("internal_error", "Something went wrong.", _rid(request)))

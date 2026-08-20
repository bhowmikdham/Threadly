"""Error envelope (R18): every non-2xx body is {"error": {code, message, detail}}.

Raise ApiError in application code — never a bare HTTPException — so the
frontend can switch on stable machine codes.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("threadly")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail=None):
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def envelope(code: str, message: str, detail=None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail}}


def not_implemented(feature: str, week: str) -> ApiError:
    """Skeleton endpoints raise this so the frontend integrates against real shapes early."""
    return ApiError(
        status=501,
        code="not_implemented",
        message=f"{feature} is not built yet (planned {week}).",
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status, content=envelope(exc.code, exc.message, exc.detail)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        code = codes.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=envelope(code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=envelope("validation_error", "Request failed validation.", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=envelope("internal_error", "Something went wrong on our side."),
        )

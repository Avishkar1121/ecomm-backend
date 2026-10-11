"""One error shape for the whole API:
{"error": {"code": "NOT_FOUND", "message": "...", "details": null}}
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

DEFAULT_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "TOO_MANY_REQUESTS",
}


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def api_error(status_code: int, code: str, message: str) -> StarletteHTTPException:
    """Raise this for errors the frontend should branch on, e.g. OUT_OF_STOCK."""
    return StarletteHTTPException(status_code, detail={"code": code, "message": message})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            code, message = exc.detail["code"], exc.detail.get("message", "")
        else:
            code = DEFAULT_CODES.get(exc.status_code, "ERROR")
            message = str(exc.detail)
        return JSONResponse(_body(code, message), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                   for e in exc.errors()]
        return JSONResponse(_body("VALIDATION_ERROR", "Invalid request", details), status_code=422)
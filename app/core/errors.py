"""One error shape for the whole API:

  general:     {"success": false, "message": "...", "code": "NOT_FOUND"}
  validation:  {"success": false, "message": "Validation failed.",
                "errors": {"email": "Please provide a valid email address."}}
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("markethub.errors")

DEFAULT_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "TOO_MANY_REQUESTS",
}


# ------------------------------------------------------------------ models (used by Swagger)
class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    code: str | None = None


class ValidationErrorResponse(BaseModel):
    success: bool = False
    message: str = "Validation failed."
    errors: dict[str, str]


# ------------------------------------------------------------------ raising
def api_error(status_code: int, code: str, message: str) -> StarletteHTTPException:
    """Raise this for errors the frontend should branch on, e.g. OUT_OF_STOCK."""
    return StarletteHTTPException(status_code, detail={"code": code, "message": message})


# ------------------------------------------------------------------ validation messages
_LABELS = {"email": "Email", "password": "Password", "new_password": "Password",
           "full_name": "Full name", "phone": "Phone", "token": "Token"}


def _field_message(field: str, err: dict) -> str:
    label = _LABELS.get(field, field.replace("_", " ").capitalize())
    kind = err["type"]
    if kind == "missing":
        return f"{label} is required."
    if kind == "string_too_short":
        n = err.get("ctx", {}).get("min_length", 1)
        return f"{label} is required." if n == 1 else f"{label} must be at least {n} characters long."
    if kind == "string_too_long":
        return f"{label} must be at most {err.get('ctx', {}).get('max_length')} characters long."
    if "email" in field and kind.startswith("value_error"):
        return "Please provide a valid email address."
    return err["msg"].removeprefix("Value error, ")


def _patch_openapi(app: FastAPI) -> None:
    """Validation errors return 400, so drop FastAPI's default 422 entry from Swagger."""
    def custom():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version,
                             description=app.description, routes=app.routes)
        for path in schema.get("paths", {}).values():
            for op in path.values():
                if isinstance(op, dict):
                    op.get("responses", {}).pop("422", None)
        comps = schema.get("components", {}).get("schemas", {})
        comps.pop("HTTPValidationError", None)
        comps.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema
    app.openapi = custom


# ------------------------------------------------------------------ handlers
def register_error_handlers(app: FastAPI) -> None:
    _patch_openapi(app)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            code, message = exc.detail["code"], exc.detail.get("message", "")
        else:
            code = DEFAULT_CODES.get(exc.status_code, "ERROR")
            message = str(exc.detail)
        return JSONResponse({"success": False, "message": message, "code": code},
                            status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors: dict[str, str] = {}
        for err in exc.errors():
            parts = [str(p) for p in err["loc"] if p not in ("body", "query", "path")]
            field = ".".join(parts) or "body"
            errors.setdefault(field, _field_message(field, err))   # first error per field
        return JSONResponse({"success": False, "message": "Validation failed.", "errors": errors},
                            status_code=400)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        """A database constraint was hit and nobody handled it: answer 4xx instead of a bare 500.
        (Routes that need a specific message, like register, still catch it themselves.)"""
        state = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if state == "23505":      # unique_violation
            code, status, message = "DUPLICATE", 409, "A record with the same unique value (e.g. slug, name or SKU) already exists."
        elif state == "23503":    # foreign_key_violation
            code, status, message = "REFERENCE_ERROR", 400, "A referenced record does not exist, or this record is still in use."
        elif state in ("23502", "23514"):   # not_null / check violation
            code, status, message = "INVALID_DATA", 400, "The data violates a database rule."
        else:
            log.exception("Unhandled database integrity error")
            code, status, message = "INTERNAL_ERROR", 500, "Internal server error."
        return JSONResponse({"success": False, "message": message, "code": code}, status_code=status)
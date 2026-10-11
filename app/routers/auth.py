import ipaddress
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.errors import api_error
from ..core.database import get_db
from ..deps import fetch_user, get_current_user
from ..core.security import (burn_password_check, create_access_token, hash_password, hash_token,
                             new_opaque_token, verify_password)
from typing import Annotated
from pydantic import AfterValidator, EmailStr
log = logging.getLogger("markethub.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "refresh_token"
COOKIE_PATH = "/api/v1/auth"          # browser only sends the cookie to auth endpoints
REUSE_GRACE_SECONDS = 10              # tolerate two tabs refreshing at the same moment

LowerEmail = Annotated[EmailStr, AfterValidator(lambda e: e.strip().lower())]

# ------------------------------------------------------------------ schemas
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: LowerEmail
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{10,15}$")


class LoginIn(BaseModel):
    email: LowerEmail
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class ForgotIn(BaseModel):
    email: LowerEmail


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


# ------------------------------------------------------------------ helpers
def _client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _issue_refresh_token(db: Session, request: Request, user_id, remember: bool) -> str:
    raw, hashed = new_opaque_token()
    days = settings.refresh_token_days_remember if remember else settings.refresh_token_days_session
    db.execute(text("""
        INSERT INTO auth_tokens (user_id, type, token_hash, user_agent, ip_address, expires_at)
        VALUES (:uid, 'refresh', :h, :ua, CAST(:ip AS inet), now() + make_interval(days => :d))
    """), {"uid": user_id, "h": hashed, "ua": (request.headers.get("user-agent") or "")[:300],
           "ip": _client_ip(request), "d": days})
    return raw


def _set_refresh_cookie(response: Response, raw: str, remember: bool) -> None:
    days = settings.refresh_token_days_remember
    response.set_cookie(
        COOKIE_NAME, raw, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path=COOKIE_PATH,
        max_age=days * 86400 if remember else None,   # None = deleted when the browser closes
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)


def _session_expired() -> JSONResponse:
    """401 that also clears the dead cookie (a raised exception would drop the Set-Cookie header)."""
    resp = JSONResponse(
        {"error": {"code": "SESSION_EXPIRED", "message": "Session expired", "details": None}},
        status_code=401)
    _clear_refresh_cookie(resp)
    return resp


def _authenticate(db: Session, email: str, password: str):
    user = db.execute(text("""
        SELECT id, password_hash FROM users
         WHERE email = :email AND deleted_at IS NULL AND is_active
    """), {"email": email}).mappings().first()
    if not user:
        burn_password_check(password)
    if not user or not verify_password(user["password_hash"], password):
        # one generic message, so attackers cannot tell which emails exist
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    db.execute(text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": user["id"]})
    return user["id"]


# ------------------------------------------------------------------ endpoints
@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    # Registration ALWAYS creates a buyer. Sellers apply separately and an admin approves.
    parts = body.full_name.strip().split(None, 1)
    first, last = parts[0][:80], (parts[1][:80] if len(parts) > 1 else "")
    try:
        user_id = db.execute(text("""
            INSERT INTO users (first_name, last_name, email, password_hash, phone)
            VALUES (:f, :l, :email, :pw, :phone) RETURNING id
        """), {"f": first, "l": last, "email": body.email,
               "pw": hash_password(body.password), "phone": body.phone}).scalar_one()
        db.execute(text("""
            INSERT INTO user_roles (user_id, role_id)
            SELECT :uid, id FROM roles WHERE name = 'buyer'
        """), {"uid": user_id})
        db.commit()   # user + role are created together or not at all
    except IntegrityError as e:
        db.rollback()
        constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None)
        if constraint in ("uq_users_email_active", "uq_users_phone_active"):
            raise api_error(409, "ALREADY_REGISTERED", "Email or phone already registered")
        raise api_error(422, "INVALID_DATA", "Invalid data")
    return {"id": user_id}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user_id = _authenticate(db, body.email, body.password)
    raw = _issue_refresh_token(db, request, user_id, body.remember_me)
    user = fetch_user(db, user_id)
    db.commit()
    _set_refresh_cookie(response, raw, body.remember_me)
    return {"access_token": create_access_token(user_id), "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60, "user": user}


@router.post("/token", include_in_schema=True)
def swagger_token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Form-based login used only by the Swagger 'Authorize' button (no cookie)."""
    user_id = _authenticate(db, form.username.strip().lower(), form.password)
    db.commit()
    return {"access_token": create_access_token(user_id), "token_type": "bearer"}


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Swap the refresh cookie for a new access token. Every refresh token works once (rotation)."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return _session_expired()
    hashed = hash_token(raw)

    # Atomic: only one request can ever consume a given token
    row = db.execute(text("""
        UPDATE auth_tokens SET used_at = now()
         WHERE token_hash = :h AND type = 'refresh'
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
        RETURNING user_id, (expires_at - created_at) > interval '2 days' AS remember
    """), {"h": hashed}).mappings().first()

    if not row:
        info = db.execute(text("""
            SELECT user_id,
                   used_at IS NOT NULL AND used_at <  now() - make_interval(secs => :g) AS stale_reuse,
                   used_at IS NOT NULL AND used_at >= now() - make_interval(secs => :g) AS recent_reuse
              FROM auth_tokens WHERE token_hash = :h AND type = 'refresh'
        """), {"h": hashed, "g": REUSE_GRACE_SECONDS}).mappings().first()

        if info and info["recent_reuse"]:
            # Two tabs raced. The winner already set a new cookie, so do NOT clear it.
            db.commit()
            return JSONResponse(
                {"error": {"code": "REFRESH_RACE", "message": "Retry", "details": None}},
                status_code=401)

        if info and info["stale_reuse"]:
            db.execute(text("""
                UPDATE auth_tokens SET revoked_at = now()
                 WHERE user_id = :u AND type = 'refresh' AND revoked_at IS NULL
            """), {"u": info["user_id"]})
            db.commit()
            log.warning("Refresh token reuse detected; revoked all sessions for user %s",
                        info["user_id"])
        return _session_expired()

    user = fetch_user(db, row["user_id"])
    if not user:
        db.commit()
        return _session_expired()

    new_raw = _issue_refresh_token(db, request, row["user_id"], row["remember"])
    db.commit()
    _set_refresh_cookie(response, new_raw, row["remember"])
    return {"access_token": create_access_token(row["user_id"]), "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60, "user": user}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        db.execute(text("""
            UPDATE auth_tokens SET revoked_at = now()
             WHERE token_hash = :h AND type = 'refresh' AND revoked_at IS NULL
        """), {"h": hash_token(raw)})
        db.commit()
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Always answers the same way, so it never reveals which emails are registered."""
    user_id = db.execute(text("""
        SELECT id FROM users WHERE email = :e AND deleted_at IS NULL AND is_active
    """), {"e": body.email}).scalar()
    if user_id:
        raw, hashed = new_opaque_token()
        db.execute(text("""
            INSERT INTO auth_tokens (user_id, type, token_hash, expires_at)
            VALUES (:u, 'password_reset', :h, now() + interval '30 minutes')
        """), {"u": user_id, "h": hashed})
        db.commit()
        # DEV ONLY: no email service yet, so the link is printed in the server log.
        log.warning("PASSWORD RESET LINK for %s: %s/reset-password?token=%s",
                    body.email, settings.frontend_url, raw)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    row = db.execute(text("""
        UPDATE auth_tokens SET used_at = now()
         WHERE token_hash = :h AND type = 'password_reset'
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
        RETURNING user_id
    """), {"h": hash_token(body.token)}).mappings().first()
    if not row:
        raise api_error(400, "INVALID_TOKEN", "This reset link is invalid or has expired")
    db.execute(text("UPDATE users SET password_hash = :pw WHERE id = :u"),
               {"pw": hash_password(body.new_password), "u": row["user_id"]})
    # sign the user out everywhere
    db.execute(text("""
        UPDATE auth_tokens SET revoked_at = now()
         WHERE user_id = :u AND type = 'refresh' AND revoked_at IS NULL
    """), {"u": row["user_id"]})
    db.commit()
    return {"message": "Password updated. Please sign in."}
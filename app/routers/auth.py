import hmac
import ipaddress
import logging
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import AfterValidator, BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.errors import api_error
from ..core.database import get_db
from ..deps import fetch_user, get_current_user
from ..core.security import (burn_password_check, create_access_token, hash_password, hash_token,
                             new_opaque_token, verify_password)

log = logging.getLogger("markethub.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "refresh_token"
COOKIE_PATH = "/api/v1/auth"          # browser only sends the cookie to auth endpoints
REUSE_GRACE_SECONDS = 10              # tolerate two tabs refreshing at the same moment
OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS_PER_HOUR = 5
OTP_MAX_ATTEMPTS = 5

LowerEmail = Annotated[EmailStr, AfterValidator(lambda e: e.strip().lower())]
Channel = Literal["email", "phone"]

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


# ------------------------------------------------------------------ response models (Swagger docs)
class TokenData(BaseModel):
    accessToken: str
    tokenType: str = "bearer"
    expiresIn: int
    refreshToken: str


class AccessData(BaseModel):
    accessToken: str
    tokenType: str = "bearer"
    expiresIn: int


class TokenResponse(BaseModel):
    success: bool = True
    message: str
    data: TokenData


class AccessResponse(BaseModel):
    success: bool = True
    message: str
    data: AccessData


class MessageResponse(BaseModel):
    success: bool = True
    message: str
    data: None = None


class ProfileResponse(BaseModel):
    success: bool = True
    message: str
    data: dict


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    code: str | None = None


class ValidationErrorResponse(BaseModel):
    success: bool = False
    message: str = "Validation failed."
    errors: dict[str, str]


class OtpSendIn(BaseModel):
    channel: Channel


class OtpVerifyIn(BaseModel):
    channel: Channel
    code: str = Field(pattern=rf"^[0-9]{{{OTP_LENGTH}}}$")

_VALIDATION = {"model": ValidationErrorResponse, "description": "Validation failed"}
_UNAUTHORIZED = {"model": ErrorResponse, "description": "Unauthorized"}


# ------------------------------------------------------------------ response helpers
def _ok(message: str, data=None) -> dict:
    """Single success envelope used by every endpoint."""
    return {"success": True, "message": message, "data": data}


def _fail(status_code: int, message: str, code: str) -> JSONResponse:
    """Single error envelope for responses we build by hand (they must carry Set-Cookie)."""
    return JSONResponse({"success": False, "message": message, "code": code},
                        status_code=status_code)


def _token_data(user_id, claims: dict | None = None, refresh: str | None = None) -> dict:
    data = {"accessToken": create_access_token(user_id, claims or {}),
            "tokenType": "bearer",
            "expiresIn": settings.access_token_minutes * 60}
    if refresh:
        data["refreshToken"] = refresh
    return data


def _claims(db: Session, user_id) -> dict:
    """Everything the client needs to treat the user as logged in, read straight from the JWT."""
    row = db.execute(text("""
        SELECT u.email, u.first_name, u.last_name,
               COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
          FROM users u
          LEFT JOIN user_roles ur ON ur.user_id = u.id
          LEFT JOIN roles r ON r.id = ur.role_id
         WHERE u.id = :u
         GROUP BY u.id
    """), {"u": user_id}).mappings().first()
    return {"email": row["email"],
            "name": f'{row["first_name"]} {row["last_name"]}'.strip(),
            "roles": list(row["roles"])}


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
    resp = _fail(401, "Session expired.", "SESSION_EXPIRED")
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
        raise api_error(401, "INVALID_CREDENTIALS", "Invalid email or password.")
    db.execute(text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": user["id"]})
    return user["id"]

def _otp_type(channel: str) -> str:
    return f"otp_{channel}"                      # 'otp_email' | 'otp_phone'

def _otp_hash(user_id, channel: str, code: str) -> str:
    # bound to user + channel so a code is useless anywhere else
    return hash_token(f"{user_id}:{channel}:{code}")

def _deliver_otp(channel: str, destination: str, code: str) -> None:
    # DEV ONLY: no email/SMS provider yet. Replace with your SES / Twilio / MSG91 call.
    log.warning("OTP for %s %s: %s", channel, destination, code)




# ------------------------------------------------------------------ endpoints
@router.post("/register", status_code=201, response_model=TokenResponse,
             responses={400: _VALIDATION,
                        409: {"model": ErrorResponse, "description": "Email or phone already registered"}})
def register(body: RegisterIn, request: Request, response: Response,
             db: Session = Depends(get_db)):
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
        raw = _issue_refresh_token(db, request, user_id, remember=False)
        claims = _claims(db, user_id)
        db.commit()   # user + role + first session are created together or not at all
    except IntegrityError as e:
        db.rollback()
        constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None)
        if constraint in ("uq_users_email_active", "uq_users_phone_active"):
            raise api_error(409, "ALREADY_REGISTERED", "Email or phone already registered.")
        raise api_error(422, "INVALID_DATA", "Invalid data.")
    _set_refresh_cookie(response, raw, remember=False)
    return _ok("Registration successful.", _token_data(user_id, claims, refresh=raw))


@router.post("/login", status_code=200, response_model=TokenResponse,
             responses={400: _VALIDATION, 401: _UNAUTHORIZED})
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user_id = _authenticate(db, body.email, body.password)
    raw = _issue_refresh_token(db, request, user_id, body.remember_me)
    claims = _claims(db, user_id)
    db.commit()
    _set_refresh_cookie(response, raw, body.remember_me)
    return _ok("Login successful.", _token_data(user_id, claims, refresh=raw))

# ------------------------------------------------------------------ otp endpoints
@router.post("/otp/send", response_model=MessageResponse,
             responses={400: _VALIDATION, 401: _UNAUTHORIZED,
                        409: {"model": ErrorResponse, "description": "Already verified"},
                        429: {"model": ErrorResponse, "description": "Too many requests"}})
def send_otp(body: OtpSendIn, user: dict = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Send a one-time code to the logged-in user's own email or phone."""
    user_id = user["id"]
    row = db.execute(text("""
        SELECT email, phone, email_verified_at, phone_verified_at
          FROM users WHERE id = :u AND deleted_at IS NULL AND is_active
    """), {"u": user_id}).mappings().first()
    if not row:
        raise api_error(401, "UNAUTHORIZED", "Not authenticated.")

    destination = row[body.channel]
    if not destination:
        raise api_error(400, "NO_DESTINATION", f"No {body.channel} on this account.")
    if row[f"{body.channel}_verified_at"]:
        raise api_error(409, "ALREADY_VERIFIED", f"Your {body.channel} is already verified.")

    otp_type = _otp_type(body.channel)
    stats = db.execute(text("""
        SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS last_hour,
               max(created_at) AS last_sent,
               EXTRACT(EPOCH FROM now() - max(created_at)) AS since_last
          FROM auth_tokens WHERE user_id = :u AND type = :t
    """), {"u": user_id, "t": otp_type}).mappings().first()
    if stats["since_last"] is not None and stats["since_last"] < OTP_RESEND_COOLDOWN_SECONDS:
        raise api_error(429, "OTP_COOLDOWN", "Please wait a minute before requesting another code.")
    if stats["last_hour"] >= OTP_MAX_SENDS_PER_HOUR:
        raise api_error(429, "OTP_LIMIT", "Too many codes requested. Try again later.")

    # only the newest code is ever valid
    db.execute(text("""
        UPDATE auth_tokens SET revoked_at = now()
         WHERE user_id = :u AND type = :t AND used_at IS NULL AND revoked_at IS NULL
    """), {"u": user_id, "t": otp_type})

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    db.execute(text("""
        INSERT INTO auth_tokens (user_id, type, token_hash, expires_at)
        VALUES (:u, :t, :h, now() + make_interval(mins => :m))
    """), {"u": user_id, "t": otp_type, "h": _otp_hash(user_id, body.channel, code),
           "m": OTP_TTL_MINUTES})
    db.commit()

    _deliver_otp(body.channel, destination, code)
    return _ok(f"Verification code sent to your {body.channel}.")

@router.post("/otp/verify", response_model=MessageResponse,
             responses={400: {"model": ErrorResponse, "description": "Invalid or expired code"},
                        401: _UNAUTHORIZED,
                        429: {"model": ErrorResponse, "description": "Too many attempts"}})
def verify_otp(body: OtpVerifyIn, user: dict = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Check the code and mark the email/phone as verified."""
    user_id = user["id"]
    otp_type = _otp_type(body.channel)

    # lock the active code so parallel guesses cannot dodge the attempt counter
    tok = db.execute(text("""
        SELECT id, token_hash, attempts FROM auth_tokens
         WHERE user_id = :u AND type = :t
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
         ORDER BY created_at DESC LIMIT 1
           FOR UPDATE
    """), {"u": user_id, "t": otp_type}).mappings().first()
    if not tok:
        db.commit()
        raise api_error(400, "INVALID_OTP", "Invalid or expired code.")

    if tok["attempts"] >= OTP_MAX_ATTEMPTS:
        db.execute(text("UPDATE auth_tokens SET revoked_at = now() WHERE id = :i"),
                   {"i": tok["id"]})
        db.commit()
        raise api_error(429, "OTP_ATTEMPTS", "Too many wrong attempts. Request a new code.")

    if not hmac.compare_digest(tok["token_hash"], _otp_hash(user_id, body.channel, body.code)):
        db.execute(text("UPDATE auth_tokens SET attempts = attempts + 1 WHERE id = :i"),
                   {"i": tok["id"]})
        db.commit()
        raise api_error(400, "INVALID_OTP", "Invalid or expired code.")

    db.execute(text("UPDATE auth_tokens SET used_at = now() WHERE id = :i"), {"i": tok["id"]})
    # column names come from a Literal-validated value, never from raw input
    db.execute(text(f"UPDATE users SET {body.channel}_verified_at = now() WHERE id = :u"),
               {"u": user_id})
    db.commit()
    return _ok(f"Your {body.channel} has been verified.")


@router.post("/token", include_in_schema=True)
def swagger_token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Form-based login used only by the Swagger 'Authorize' button (no cookie).
    Keeps the standard OAuth2 shape on purpose: Swagger requires access_token/token_type."""
    user_id = _authenticate(db, form.username.strip().lower(), form.password)
    db.commit()
    return {"access_token": create_access_token(user_id), "token_type": "bearer"}


@router.post("/refresh", response_model=AccessResponse, responses={401: _UNAUTHORIZED})
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
            return _fail(401, "Retry.", "REFRESH_RACE")

        if info and info["stale_reuse"]:
            db.execute(text("""
                UPDATE auth_tokens SET revoked_at = now()
                 WHERE user_id = :u AND type = 'refresh' AND revoked_at IS NULL
            """), {"u": info["user_id"]})
            db.commit()
            log.warning("Refresh token reuse detected; revoked all sessions for user %s",
                        info["user_id"])
        return _session_expired()

    # Cheap existence check instead of loading the full user profile on every refresh.
    alive = db.execute(text("""
        SELECT 1 FROM users WHERE id = :u AND deleted_at IS NULL AND is_active
    """), {"u": row["user_id"]}).scalar()
    if not alive:
        db.commit()
        return _session_expired()

    new_raw = _issue_refresh_token(db, request, row["user_id"], row["remember"])
    db.commit()
    claims = _claims(db, row["user_id"])
    _set_refresh_cookie(response, new_raw, row["remember"])
    return _ok("Token refreshed.", _token_data(row["user_id"], claims))


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


@router.get("/me", response_model=ProfileResponse, responses={401: _UNAUTHORIZED})
def me(user: dict = Depends(get_current_user)):
    return _ok("Profile fetched.", user)


@router.post("/forgot-password", response_model=MessageResponse, responses={400: _VALIDATION})
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
    return _ok("If that email is registered, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse,
             responses={400: {"model": ErrorResponse, "description": "Invalid token or validation failed"}})
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    row = db.execute(text("""
        UPDATE auth_tokens SET used_at = now()
         WHERE token_hash = :h AND type = 'password_reset'
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
        RETURNING user_id
    """), {"h": hash_token(body.token)}).mappings().first()
    if not row:
        raise api_error(400, "INVALID_TOKEN", "This reset link is invalid or has expired.")
    db.execute(text("UPDATE users SET password_hash = :pw WHERE id = :u"),
               {"pw": hash_password(body.new_password), "u": row["user_id"]})
    # sign the user out everywhere
    db.execute(text("""
        UPDATE auth_tokens SET revoked_at = now()
         WHERE user_id = :u AND type = 'refresh' AND revoked_at IS NULL
    """), {"u": row["user_id"]})
    db.commit()
    return _ok("Password updated. Please sign in.")
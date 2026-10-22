import hmac
import ipaddress
import logging
import secrets
import uuid
from typing import Annotated, Literal

import phonenumbers
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import AfterValidator, BaseModel, EmailStr, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.errors import api_error
from ..core.database import get_db
from ..core.mailer import send_email
from ..core.rate_limit import client_key, limiter
from ..deps import fetch_user, get_current_user, get_otp_user
from ..core.security import (VERIFY_TOKEN_MINUTES, burn_password_check, create_access_token,
                             create_verify_token, hash_password, hash_token, new_opaque_token,
                             verify_password)

log = logging.getLogger("markethub.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "refresh_token"
COOKIE_PATH = "/api/v1/auth"          # browser only sends the cookie to auth endpoints
REUSE_GRACE_SECONDS = 10              # tolerate two tabs refreshing at the same moment
OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS_PER_HOUR = 5            # per user, per channel
OTP_MAX_SENDS_PER_IP_HOUR = 20        # per IP, all channels (SMS-pumping guard)
OTP_MAX_ATTEMPTS = 5
DEFAULT_PHONE_REGION = "IN"

# Rate limits (in-memory, per process; see core/rate_limit.py)
LOGIN_MAX_PER_IP = (30, 600)             # 30 attempts / 10 min per IP
LOGIN_MAX_PER_IP_EMAIL = (8, 900)        # 8 attempts / 15 min per IP+email pair
REGISTER_MAX_PER_IP = (10, 3600)
FORGOT_MAX_PER_IP = (5, 3600)
FORGOT_MAX_PER_EMAIL = (3, 3600)
RESET_MAX_PER_IP = (10, 3600)


def _normalize_phone(value: str | None) -> str | None:
    """Any common Indian/international format -> E.164 (+919876543210)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        num = phonenumbers.parse(value, DEFAULT_PHONE_REGION)
    except phonenumbers.NumberParseException:
        raise ValueError("Invalid phone number.")
    if not phonenumbers.is_valid_number(num):
        raise ValueError("Invalid phone number.")
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


LowerEmail = Annotated[EmailStr, AfterValidator(lambda e: e.strip().lower())]
Phone = Annotated[str, AfterValidator(_normalize_phone)]
Channel = Literal["email", "phone"]

# ------------------------------------------------------------------ schemas
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: LowerEmail
    password: str = Field(min_length=8, max_length=128)
    phone: Phone | None = Field(default=None, max_length=25)
    verify_via: Channel = "email"        # the ONE channel that gets the signup OTP

    @model_validator(mode="after")
    def _phone_needed_for_phone_otp(self):
        if self.verify_via == "phone" and not self.phone:
            raise ValueError("A phone number is required to verify via phone.")
        return self


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


class VerifyData(BaseModel):
    verificationToken: str          # limited token: only works on /otp/send and /otp/verify
    tokenType: str = "bearer"
    expiresIn: int


class VerifyResponse(BaseModel):
    success: bool = True
    message: str
    data: VerifyData


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
    data: dict | None = None


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


def _token_data(user_id, claims: dict | None = None) -> dict:
    """The refresh token is NEVER put in the body: it lives only in the httpOnly cookie."""
    return {"accessToken": create_access_token(user_id, claims or {}),
            "tokenType": "bearer",
            "expiresIn": settings.access_token_minutes * 60}


def _verify_token_data(user_id, claims: dict) -> dict:
    return {"verificationToken": create_verify_token(user_id, claims),
            "tokenType": "bearer",
            "expiresIn": VERIFY_TOKEN_MINUTES * 60}


def _is_activated(claims: dict) -> bool:
    """An account may start a real session once at least one channel is verified."""
    return bool(claims["email_verified"] or claims["phone_verified"])


def _verification_required(user_id, claims: dict) -> JSONResponse:
    """403 that carries the limited token, so the client can go straight to the OTP screen."""
    return JSONResponse({"success": False,
                         "message": "Verify your email or phone to continue.",
                         "code": "VERIFICATION_REQUIRED",
                         "data": _verify_token_data(user_id, claims)}, status_code=403)


def _claims(db: Session, user_id) -> dict:
    """Everything the client needs to treat the user as logged in, read straight from the JWT."""
    row = db.execute(text("""
        SELECT u.email, u.first_name, u.last_name,
               (u.email_verified_at IS NOT NULL) AS email_verified,
               (u.phone_verified_at IS NOT NULL) AS phone_verified,
               COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
          FROM users u
          LEFT JOIN user_roles ur ON ur.user_id = u.id
          LEFT JOIN roles r ON r.id = ur.role_id
         WHERE u.id = :u
         GROUP BY u.id
    """), {"u": user_id}).mappings().first()
    return {"email": row["email"],
            "name": f'{row["first_name"]} {row["last_name"]}'.strip(),
            "email_verified": row["email_verified"],
            "phone_verified": row["phone_verified"],
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
        INSERT INTO auth_tokens (user_id, type, token_hash, user_agent, ip_address, expires_at, remember)
        VALUES (:uid, 'refresh', :h, :ua, CAST(:ip AS inet), now() + make_interval(days => :d), :rem)
    """), {"uid": user_id, "h": hashed, "ua": (request.headers.get("user-agent") or "")[:300],
           "ip": _client_ip(request), "d": days, "rem": remember})
    return raw


def _set_refresh_cookie(response: Response, raw: str, remember: bool) -> None:
    days = settings.refresh_token_days_remember
    response.set_cookie(
        COOKIE_NAME, raw, httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, path=COOKIE_PATH,
        max_age=days * 86400 if remember else None,   # None = deleted when the browser closes
    )


def _clear_refresh_cookie(response: Response) -> None:
    # attributes must match set_cookie, otherwise SameSite=None cookies are not removed
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True,
                           secure=settings.cookie_secure, samesite=settings.cookie_samesite)


def _session_expired() -> JSONResponse:
    """401 that also clears the dead cookie (a raised exception would drop the Set-Cookie header)."""
    resp = _fail(401, "Session expired.", "SESSION_EXPIRED")
    _clear_refresh_cookie(resp)
    return resp


def _throttle_login(request: Request, email: str) -> None:
    ip = client_key(request)
    limiter.check(f"login:ip:{ip}", *LOGIN_MAX_PER_IP)
    # keyed on IP+email (not email alone) so a stranger cannot lock the real owner out
    limiter.check(f"login:pair:{ip}:{email}", *LOGIN_MAX_PER_IP_EMAIL)


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


# ------------------------------------------------------------------ otp helpers
def _otp_type(channel: str) -> str:
    return f"otp_{channel}"                      # 'otp_email' | 'otp_phone'


def _otp_hash(token_id, user_id, channel: str, destination: str, code: str) -> str:
    """Keyed hash bound to token row + user + channel + the exact email/phone the code was sent to.
    - pepper: a leaked auth_tokens table cannot be brute-forced (only 10^6 codes exist)
    - destination: changing email/phone invalidates every code sent to the old one
    - token_id: keeps token_hash UNIQUE even if the same 6-digit code is generated twice"""
    msg = f"{token_id}:{user_id}:{channel}:{destination}:{code}".encode()
    return hmac.new(settings.otp_pepper.encode(), msg, "sha256").hexdigest()


def _deliver_otp(channel: str, destination: str, code: str) -> None:
    """Runs in a background task, so a slow provider never blocks the request.
    Email goes out over SMTP (core/mailer.py). SMS is still a stub: plug in Twilio / MSG91 here.
    If delivery fails, the user just resends."""
    try:
        if settings.otp_dev_log:
            # DEV ONLY (set OTP_DEV_LOG=true locally). Refused at startup when ENV=prod.
            log.warning("OTP for %s %s: %s", channel, destination, code)
            return
        if channel == "email":
            send_email(destination, f"Your {settings.app_name} verification code",
                       f"Your verification code is {code}.\n\n"
                       f"It expires in {OTP_TTL_MINUTES} minutes. If you did not request it, ignore this email.")
            return
        raise NotImplementedError("No SMS provider configured")
    except Exception:
        # never log the code itself here
        log.exception("OTP delivery failed (channel=%s)", channel)


def _issue_otp(db: Session, user_id, channel: str, destination: str, ip: str | None) -> str:
    """Revoke older codes, store a fresh one, return the plain code. Caller commits and delivers."""
    otp_type = _otp_type(channel)
    # only the newest code is ever valid
    db.execute(text("""
        UPDATE auth_tokens SET revoked_at = now()
         WHERE user_id = :u AND type = :t AND used_at IS NULL AND revoked_at IS NULL
    """), {"u": user_id, "t": otp_type})
    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    token_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO auth_tokens (id, user_id, type, token_hash, ip_address, expires_at)
        VALUES (:id, :u, :t, :h, CAST(:ip AS inet), now() + make_interval(mins => :m))
    """), {"id": token_id, "u": user_id, "t": otp_type,
           "h": _otp_hash(token_id, user_id, channel, destination, code),
           "ip": ip, "m": OTP_TTL_MINUTES})
    return code


def require_verified(first: Channel, *rest: Channel):
    """Route guard. Reads the DB (not the JWT) so it is correct right after verification.
    Usage: Depends(require_verified("phone"))  or  Depends(require_verified("email", "phone"))
    -> move to deps.py if you prefer."""
    needed = (first, *rest)

    def dep(user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
        row = db.execute(text("""
            SELECT email_verified_at, phone_verified_at FROM users
             WHERE id = :u AND deleted_at IS NULL AND is_active
        """), {"u": user["id"]}).mappings().first()
        if not row:
            raise api_error(401, "UNAUTHORIZED", "Not authenticated.")
        missing = [c for c in needed if not row[f"{c}_verified_at"]]
        if missing:
            raise api_error(403, "VERIFICATION_REQUIRED",
                            f"Please verify your {' and '.join(missing)} to continue.")
        return user
    return dep


# ------------------------------------------------------------------ endpoints
@router.post("/register", status_code=201, response_model=VerifyResponse,
             responses={400: _VALIDATION,
                        409: {"model": ErrorResponse, "description": "Email or phone already registered"}})
def register(body: RegisterIn, request: Request, response: Response,
             background: BackgroundTasks, db: Session = Depends(get_db)):
    limiter.check(f"register:ip:{client_key(request)}", *REGISTER_MAX_PER_IP)
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
        ip = _client_ip(request)
        # ONE code, to the channel the user chose. Only that channel becomes verified.
        destination = body.email if body.verify_via == "email" else body.phone
        code = _issue_otp(db, user_id, body.verify_via, destination, ip)
        claims = _claims(db, user_id)
        db.commit()   # user + role + OTP are created together or not at all
    except IntegrityError as e:
        db.rollback()
        constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None)
        if constraint in ("uq_users_email_active", "uq_users_phone_active"):
            raise api_error(409, "ALREADY_REGISTERED", "Email or phone already registered.")
        raise api_error(422, "INVALID_DATA", "Invalid data.")
    background.add_task(_deliver_otp, body.verify_via, destination, code)
    # No session yet: only a limited token that can call the OTP endpoints.
    return _ok(f"Registration successful. Enter the code sent to your {body.verify_via}.",
               _verify_token_data(user_id, claims))


@router.post("/login", status_code=200, response_model=TokenResponse,
             responses={400: _VALIDATION, 401: _UNAUTHORIZED,
                        403: {"model": ErrorResponse, "description": "Account not verified yet"}})
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _throttle_login(request, body.email)
    user_id = _authenticate(db, body.email, body.password)
    claims = _claims(db, user_id)
    if not _is_activated(claims):
        db.commit()
        return _verification_required(user_id, claims)
    raw = _issue_refresh_token(db, request, user_id, body.remember_me)
    db.commit()
    _set_refresh_cookie(response, raw, body.remember_me)
    return _ok("Login successful.", _token_data(user_id, claims))


# ------------------------------------------------------------------ otp endpoints
@router.post("/otp/send", response_model=MessageResponse,
             responses={400: _VALIDATION, 401: _UNAUTHORIZED,
                        409: {"model": ErrorResponse, "description": "Already verified"},
                        429: {"model": ErrorResponse, "description": "Too many requests"}})
def send_otp(body: OtpSendIn, request: Request, background: BackgroundTasks,
             user: dict = Depends(get_otp_user), db: Session = Depends(get_db)):
    """Send a one-time code to the logged-in user's own email or phone."""
    user_id = user["id"]
    ip = _client_ip(request)

    # FOR UPDATE serializes concurrent sends for this user, so the cooldown can't be raced
    row = db.execute(text("""
        SELECT email, phone, email_verified_at, phone_verified_at
          FROM users WHERE id = :u AND deleted_at IS NULL AND is_active
           FOR UPDATE
    """), {"u": user_id}).mappings().first()
    if not row:
        db.rollback()
        raise api_error(401, "UNAUTHORIZED", "Not authenticated.")

    destination = row[body.channel]
    if not destination:
        db.rollback()
        raise api_error(400, "NO_DESTINATION", f"No {body.channel} on this account.")
    if row[f"{body.channel}_verified_at"]:
        db.rollback()
        raise api_error(409, "ALREADY_VERIFIED", f"Your {body.channel} is already verified.")

    otp_type = _otp_type(body.channel)
    stats = db.execute(text("""
        SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS last_hour,
               EXTRACT(EPOCH FROM now() - max(created_at)) AS since_last
          FROM auth_tokens WHERE user_id = :u AND type = :t
    """), {"u": user_id, "t": otp_type}).mappings().first()

    if stats["since_last"] is not None and stats["since_last"] < OTP_RESEND_COOLDOWN_SECONDS:
        wait = int(OTP_RESEND_COOLDOWN_SECONDS - stats["since_last"]) + 1
        db.rollback()
        raise api_error(429, "OTP_COOLDOWN", f"Please wait {wait}s before requesting another code.")
    if stats["last_hour"] >= OTP_MAX_SENDS_PER_HOUR:
        db.rollback()
        raise api_error(429, "OTP_LIMIT", "Too many codes requested. Try again later.")

    if ip:
        ip_count = db.execute(text("""
            SELECT count(*) FROM auth_tokens
             WHERE type IN ('otp_email', 'otp_phone')
               AND ip_address = CAST(:ip AS inet)
               AND created_at > now() - interval '1 hour'
        """), {"ip": ip}).scalar_one()
        if ip_count >= OTP_MAX_SENDS_PER_IP_HOUR:
            db.rollback()
            raise api_error(429, "OTP_IP_LIMIT", "Too many codes requested. Try again later.")

    code = _issue_otp(db, user_id, body.channel, destination, ip)
    db.commit()

    background.add_task(_deliver_otp, body.channel, destination, code)
    return _ok(f"Verification code sent to your {body.channel}.")


@router.post("/otp/verify", response_model=TokenResponse,
             responses={400: {"model": ErrorResponse, "description": "Invalid or expired code"},
                        401: _UNAUTHORIZED,
                        409: {"model": ErrorResponse, "description": "Already verified or in use"},
                        429: {"model": ErrorResponse, "description": "Too many attempts"}})
def verify_otp(body: OtpVerifyIn, request: Request, response: Response,
               user: dict = Depends(get_otp_user), db: Session = Depends(get_db)):
    """Check the code and mark the email/phone as verified."""
    user_id = user["id"]
    otp_type = _otp_type(body.channel)

    # column names come from a Literal-validated value, never from raw input
    u = db.execute(text(f"""
        SELECT {body.channel} AS destination, {body.channel}_verified_at AS verified_at
          FROM users WHERE id = :u AND deleted_at IS NULL AND is_active
    """), {"u": user_id}).mappings().first()
    if not u:
        db.rollback()
        raise api_error(401, "UNAUTHORIZED", "Not authenticated.")
    if u["verified_at"]:
        db.rollback()
        raise api_error(409, "ALREADY_VERIFIED", f"Your {body.channel} is already verified.")
    if not u["destination"]:
        db.rollback()
        raise api_error(400, "NO_DESTINATION", f"No {body.channel} on this account.")

    # lock the active code so parallel guesses cannot dodge the attempt counter
    tok = db.execute(text("""
        SELECT id, token_hash FROM auth_tokens
         WHERE user_id = :u AND type = :t
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
         ORDER BY created_at DESC LIMIT 1
           FOR UPDATE
    """), {"u": user_id, "t": otp_type}).mappings().first()
    if not tok:
        db.rollback()
        raise api_error(400, "INVALID_OTP", "Invalid or expired code.")

    # hash uses the CURRENT destination: if it changed since sending, the code can't match
    expected = _otp_hash(tok["id"], user_id, body.channel, u["destination"], body.code)
    if not hmac.compare_digest(tok["token_hash"], expected):
        # increment + revoke in one statement; lockout is reported on the 5th wrong guess
        attempts = db.execute(text("""
            UPDATE auth_tokens
               SET attempts = attempts + 1,
                   revoked_at = CASE WHEN attempts + 1 >= :max THEN now() ELSE revoked_at END
             WHERE id = :i
            RETURNING attempts
        """), {"i": tok["id"], "max": OTP_MAX_ATTEMPTS}).scalar_one()
        db.commit()
        if attempts >= OTP_MAX_ATTEMPTS:
            raise api_error(429, "OTP_ATTEMPTS", "Too many wrong attempts. Request a new code.")
        raise api_error(400, "INVALID_OTP", "Invalid or expired code.")

    try:
        db.execute(text("UPDATE auth_tokens SET used_at = now() WHERE id = :i"), {"i": tok["id"]})
        db.execute(text(f"UPDATE users SET {body.channel}_verified_at = now() WHERE id = :u"),
                   {"u": user_id})
        db.commit()
    except IntegrityError:
        # only possible once uniqueness applies to verified identifiers (see migration)
        db.rollback()
        raise api_error(409, "ALREADY_IN_USE",
                        f"This {body.channel} is already verified on another account.")
    claims = _claims(db, user_id)      # fresh claims: email_verified / phone_verified updated
    message = f"Your {body.channel} has been verified."
    if user["token_type"] == "verify":
        # First verification = activation: only now does the user get a real session.
        raw = _issue_refresh_token(db, request, user_id, remember=False)
        db.commit()
        _set_refresh_cookie(response, raw, remember=False)
        return _ok(message, _token_data(user_id, claims))
    # Already logged in (verifying the second channel): just refresh the claims.
    return _ok(message, _token_data(user_id, claims))


def swagger_token(request: Request, form: OAuth2PasswordRequestForm = Depends(),
                  db: Session = Depends(get_db)):
    """Form-based login used only by the Swagger 'Authorize' button (no cookie).
    Keeps the standard OAuth2 shape on purpose: Swagger requires access_token/token_type.
    Registered ONLY when ENV=dev (see the bottom of this file)."""
    email = form.username.strip().lower()
    _throttle_login(request, email)
    user_id = _authenticate(db, email, form.password)
    claims = _claims(db, user_id)
    db.commit()
    if not _is_activated(claims):
        # Unverified: Swagger gets the limited token, which only works on the OTP endpoints.
        return {"access_token": create_verify_token(user_id), "token_type": "bearer"}
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
        RETURNING user_id, (remember OR (expires_at - created_at) > interval '2 days') AS remember
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
           AND (email_verified_at IS NOT NULL OR phone_verified_at IS NOT NULL)
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


def _deliver_reset_link(email: str, raw_token: str) -> None:
    """Background task. Never logs the token unless OTP_DEV_LOG=true (dev only)."""
    link = f"{settings.frontend_url}/reset-password?token={raw_token}"
    try:
        if settings.otp_dev_log:
            log.warning("PASSWORD RESET LINK for %s: %s", email, link)
            return
        send_email(email, f"Reset your {settings.app_name} password",
                   f"Use this link to choose a new password (valid for 30 minutes):\n\n{link}\n\n"
                   "If you did not ask for this, ignore this email.")
    except Exception:
        log.exception("Password reset email failed")


@router.post("/forgot-password", response_model=MessageResponse, responses={400: _VALIDATION})
def forgot_password(body: ForgotIn, request: Request, background: BackgroundTasks,
                    db: Session = Depends(get_db)):
    """Always answers the same way, so it never reveals which emails are registered."""
    limiter.check(f"forgot:ip:{client_key(request)}", *FORGOT_MAX_PER_IP)
    limiter.check(f"forgot:email:{body.email}", *FORGOT_MAX_PER_EMAIL)
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
        background.add_task(_deliver_reset_link, body.email, raw)
    return _ok("If that email is registered, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse,
             responses={400: {"model": ErrorResponse, "description": "Invalid token or validation failed"}})
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"reset:ip:{client_key(request)}", *RESET_MAX_PER_IP)
    row = db.execute(text("""
        UPDATE auth_tokens SET used_at = now()
         WHERE token_hash = :h AND type = 'password_reset'
           AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
        RETURNING user_id
    """), {"h": hash_token(body.token)}).mappings().first()
    if not row:
        raise api_error(400, "INVALID_TOKEN", "This reset link is invalid or has expired.")
    # the link was emailed, so using it also proves the user owns that email
    db.execute(text("""UPDATE users SET password_hash = :pw,
                              email_verified_at = COALESCE(email_verified_at, now())
                        WHERE id = :u"""),
               {"pw": hash_password(body.new_password), "u": row["user_id"]})
    # sign the user out everywhere
    db.execute(text("""
        UPDATE auth_tokens SET revoked_at = now()
         WHERE user_id = :u AND type = 'refresh' AND revoked_at IS NULL
    """), {"u": row["user_id"]})
    db.commit()
    return _ok("Password updated. Please sign in.")


# Swagger "Authorize" login. Not exposed outside dev: it skips remember-me and the cookie flow.
if settings.is_dev:
    router.add_api_route("/token", swagger_token, methods=["POST"])
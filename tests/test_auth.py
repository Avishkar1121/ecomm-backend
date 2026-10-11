"""Auth API tests for MarketHub (needs the Postgres test DB from conftest.py).

    pip install pytest httpx pyjwt
    pytest -q test_auth.py

test_refresh_race_* expects the REFRESH_RACE patch in auth.py's refresh endpoint.
Without that patch it fails: that failure is the bug being demonstrated.
"""
import hashlib
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import text

API = "/api/v1"
COOKIE = "refresh_token"
NEW = {"full_name": "New Person", "email": "new@example.com", "password": "Passw0rd!x"}



def sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def cookie_of(client):
    """Current refresh-cookie value in the client's cookie jar."""
    return next((c.value for c in client.cookies.jar if c.name == COOKIE), None)


def set_cookie_headers(resp) -> list[str]:
    return [h.lower() for h in resp.headers.get_list("set-cookie")]


def refresh_with(client, raw: str):
    """Call /auth/refresh with an explicit cookie value (another browser, or a replay)."""
    client.cookies.clear()
    return client.post(f"{API}/auth/refresh", headers={"Cookie": f"{COOKIE}={raw}"})


def assert_error(resp, status: int, code: str):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code


def lifetime_is(db, raw: str, interval: str) -> bool:
    return db.execute(text("""
        SELECT expires_at - created_at = CAST(:iv AS interval)
          FROM auth_tokens WHERE token_hash = :h
    """), {"iv": interval, "h": sha(raw)}).scalar()


def make_jwt(sub, *, typ="access", secret=None, delta=timedelta(minutes=5), alg="HS256"):
    now = datetime.now(timezone.utc)
    payload = {"sub": str(sub), "type": typ, "iat": now, "exp": now + delta}
    key = os.environ["JWT_SECRET"] if secret is None else secret
    return jwt.encode(payload, "" if alg == "none" else key, algorithm=alg)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sql(db, statement: str, **params):
    db.execute(text(statement), params)
    db.commit()


# ------------------------------------------------------------------ register
def test_register_creates_buyer_only(client, db):
    r = client.post(f"{API}/auth/register", json=NEW)
    assert r.status_code == 201
    roles = db.execute(text("""
        SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :u
    """), {"u": r.json()["id"]}).scalars().all()
    assert roles == ["buyer"]


def test_register_cannot_choose_a_role(client, db):
    """The design has a Buyer/Seller selector; the backend must ignore any role sent."""
    r = client.post(f"{API}/auth/register", json={**NEW, "role": "admin"})
    assert r.status_code == 201
    roles = db.execute(text("""
        SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :u
    """), {"u": r.json()["id"]}).scalars().all()
    assert roles == ["buyer"]


def test_register_single_word_name(client, db):
    r = client.post(f"{API}/auth/register", json={**NEW, "full_name": "Madonna"})
    assert r.status_code == 201
    row = db.execute(text("SELECT first_name, last_name FROM users WHERE id = :u"),
                     {"u": r.json()["id"]}).one()
    assert tuple(row) == ("Madonna", "")


def test_register_duplicate_email(client, user):
    r = client.post(f"{API}/auth/register", json={**NEW, "email": user["email"]})
    assert_error(r, 409, "ALREADY_REGISTERED")


def test_register_duplicate_email_ignores_case(client, user):
    r = client.post(f"{API}/auth/register", json={**NEW, "email": user["email"].upper()})
    assert_error(r, 409, "ALREADY_REGISTERED")


def test_register_duplicate_phone(client):
    phone = "+919876543210"
    assert client.post(f"{API}/auth/register", json={**NEW, "phone": phone}).status_code == 201
    r = client.post(f"{API}/auth/register",
                    json={**NEW, "email": "other@example.com", "phone": phone})
    assert_error(r, 409, "ALREADY_REGISTERED")


def test_register_can_reuse_email_of_deleted_user(client, user, db):
    sql(db, "UPDATE users SET deleted_at = now() WHERE id = :u", u=user["id"])
    assert client.post(f"{API}/auth/register", json=NEW | {"email": user["email"]}).status_code == 201


@pytest.mark.parametrize("patch", [
    {"email": "not-an-email"},
    {"password": "short"},
    {"password": "x" * 129},
    {"full_name": ""},
    {"phone": "12ab"},
])
def test_register_validation(client, patch):
    r = client.post(f"{API}/auth/register", json={**NEW, **patch})
    assert_error(r, 422, "VALIDATION_ERROR")
    field = next(iter(patch))
    assert any(d["field"] == field for d in r.json()["error"]["details"])


def test_register_does_not_log_in(client):
    r = client.post(f"{API}/auth/register", json=NEW)
    assert "access_token" not in r.json()
    assert cookie_of(client) is None


# ------------------------------------------------------------------ login
def test_login_success_shape(login, user):
    r = login()
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["user"]["email"] == user["email"]
    assert body["user"]["roles"] == ["buyer"]
    assert "password_hash" not in body["user"]
    assert "password" not in str(body["user"]).lower()


def test_login_email_is_case_insensitive(login, user):
    assert login(email=user["email"].upper()).status_code == 200


def test_login_wrong_password_and_unknown_email_look_identical(client, user):
    a = client.post(f"{API}/auth/login", json={"email": user["email"], "password": "wrong-pass"})
    b = client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert cookie_of(client) is None


def test_login_missing_password_is_422(client, user):
    r = client.post(f"{API}/auth/login", json={"email": user["email"]})
    assert_error(r, 422, "VALIDATION_ERROR")


def test_login_deactivated_user_rejected(login, user, db):
    sql(db, "UPDATE users SET is_active = false WHERE id = :u", u=user["id"])
    assert_error(login(), 401, "UNAUTHORIZED")


def test_login_soft_deleted_user_rejected(login, user, db):
    sql(db, "UPDATE users SET deleted_at = now() WHERE id = :u", u=user["id"])
    assert_error(login(), 401, "UNAUTHORIZED")


def test_login_updates_last_login_at(login, user, db):
    assert db.execute(text("SELECT last_login_at FROM users WHERE id = :u"),
                      {"u": user["id"]}).scalar() is None
    login()
    db.rollback()   # drop the old snapshot
    assert db.execute(text("SELECT last_login_at FROM users WHERE id = :u"),
                      {"u": user["id"]}).scalar() is not None


def test_login_cookie_flags_and_remember_me(login):
    r = login(remember=True)
    c = next(c for c in set_cookie_headers(r) if c.startswith(f"{COOKIE}="))
    assert "httponly" in c
    assert "path=/api/v1/auth" in c
    assert "samesite=lax" in c
    assert "max-age=2592000" in c            # 30 days


def test_login_without_remember_me_is_a_session_cookie(login):
    r = login(remember=False)
    c = next(c for c in set_cookie_headers(r) if c.startswith(f"{COOKIE}="))
    assert "httponly" in c
    assert "max-age" not in c and "expires" not in c


@pytest.mark.parametrize("remember,interval", [(True, "30 days"), (False, "1 day")])
def test_refresh_token_lifetime_in_db(client, login, db, remember, interval):
    login(remember=remember)
    assert lifetime_is(db, cookie_of(client), interval)


def test_only_the_hash_is_stored(client, login, db):
    login()
    raw = cookie_of(client)
    hashes = db.execute(text("SELECT token_hash FROM auth_tokens")).scalars().all()
    assert sha(raw) in hashes
    assert raw not in hashes


def test_login_records_user_agent(client, login, db):
    client.headers["user-agent"] = "pytest-agent/1.0"
    login()
    ua = db.execute(text("SELECT user_agent FROM auth_tokens")).scalar()
    assert ua == "pytest-agent/1.0"


# ------------------------------------------------------------------ access token / me
def test_me_with_valid_token(client, auth_header, user):
    r = client.get(f"{API}/auth/me", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["email"] == user["email"]
    assert r.json()["id"] == user["id"]


def test_me_without_token(client):
    assert_error(client.get(f"{API}/auth/me"), 401, "UNAUTHORIZED")


def test_me_garbage_token(client):
    assert_error(client.get(f"{API}/auth/me", headers=bearer("not.a.jwt")), 401, "UNAUTHORIZED")


def test_me_expired_token(client, user):
    token = make_jwt(user["id"], delta=timedelta(minutes=-1))
    assert_error(client.get(f"{API}/auth/me", headers=bearer(token)), 401, "UNAUTHORIZED")


def test_me_token_with_wrong_type(client, user):
    token = make_jwt(user["id"], typ="refresh")
    assert_error(client.get(f"{API}/auth/me", headers=bearer(token)), 401, "UNAUTHORIZED")


def test_me_token_signed_with_other_secret(client, user):
    token = make_jwt(user["id"], secret="some-other-secret-" + "y" * 30)
    assert_error(client.get(f"{API}/auth/me", headers=bearer(token)), 401, "UNAUTHORIZED")


def test_me_unsigned_token_rejected(client, user):
    token = make_jwt(user["id"], alg="none")
    assert_error(client.get(f"{API}/auth/me", headers=bearer(token)), 401, "UNAUTHORIZED")


def test_me_valid_token_for_deactivated_user(client, user, db):
    token = make_jwt(user["id"])
    assert client.get(f"{API}/auth/me", headers=bearer(token)).status_code == 200
    sql(db, "UPDATE users SET is_active = false WHERE id = :u", u=user["id"])
    assert_error(client.get(f"{API}/auth/me", headers=bearer(token)), 401, "UNAUTHORIZED")


def test_swagger_form_login(client, user):
    ok = client.post(f"{API}/auth/token",
                     data={"username": user["email"], "password": user["password"]})
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert cookie_of(client) is None         # the Swagger flow never sets the cookie
    bad = client.post(f"{API}/auth/token", data={"username": user["email"], "password": "nope-nope"})
    assert bad.status_code == 401


# ------------------------------------------------------------------ refresh
def test_refresh_rotates_the_token(client, login, db):
    login()
    old = cookie_of(client)
    r = client.post(f"{API}/auth/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["user"]["email"] == "t@x.com"
    new = cookie_of(client)
    assert new and new != old
    used = db.execute(text("SELECT used_at FROM auth_tokens WHERE token_hash = :h"),
                      {"h": sha(old)}).scalar()
    assert used is not None


def test_refreshed_access_token_works(client, login):
    login()
    token = client.post(f"{API}/auth/refresh").json()["access_token"]
    assert client.get(f"{API}/auth/me", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("remember", [True, False])
def test_rotation_keeps_the_remember_me_choice(client, login, db, remember):
    login(remember=remember)
    r = client.post(f"{API}/auth/refresh")
    c = next(c for c in set_cookie_headers(r) if c.startswith(f"{COOKIE}="))
    assert ("max-age=2592000" in c) is remember
    assert lifetime_is(db, cookie_of(client), "30 days" if remember else "1 day")


def test_refresh_without_cookie(client):
    r = client.post(f"{API}/auth/refresh")
    assert_error(r, 401, "SESSION_EXPIRED")


def test_refresh_with_garbage_cookie_clears_it(client):
    r = refresh_with(client, "garbage-" * 8)
    assert_error(r, 401, "SESSION_EXPIRED")
    cleared = [c for c in set_cookie_headers(r) if c.startswith(f"{COOKIE}=")]
    assert cleared and "max-age=0" in cleared[0]


def test_refresh_expired_token(client, login, db):
    login()
    raw = cookie_of(client)
    sql(db, "UPDATE auth_tokens SET expires_at = now() - interval '1 second' WHERE token_hash = :h",
        h=sha(raw))
    assert_error(client.post(f"{API}/auth/refresh"), 401, "SESSION_EXPIRED")


def test_refresh_for_deactivated_user(client, login, user, db):
    login()
    sql(db, "UPDATE users SET is_active = false WHERE id = :u", u=user["id"])
    assert_error(client.post(f"{API}/auth/refresh"), 401, "SESSION_EXPIRED")


def test_stale_reuse_revokes_every_session_of_that_user(client, login, db):
    login()
    a = cookie_of(client)
    login()
    b = cookie_of(client)                           # a second device, perfectly legitimate
    assert refresh_with(client, a).status_code == 200    # a is now used, a2 issued
    sql(db, "UPDATE auth_tokens SET used_at = now() - interval '1 minute' WHERE token_hash = :h",
        h=sha(a))
    r = refresh_with(client, a)                     # attacker replays the old token
    assert_error(r, 401, "SESSION_EXPIRED")
    live = db.execute(text(
        "SELECT count(*) FROM auth_tokens WHERE type = 'refresh' AND revoked_at IS NULL")).scalar()
    assert live == 0                                # a2 and the other device's b are dead too
    assert_error(refresh_with(client, b), 401, "SESSION_EXPIRED")


def test_refresh_race_does_not_log_the_user_out(client, login):
    """Two tabs refreshing at the same moment. Needs the REFRESH_RACE patch in auth.py."""
    login()
    a = cookie_of(client)
    assert refresh_with(client, a).status_code == 200      # tab 1 wins
    winner_cookie = cookie_of(client)
    r = refresh_with(client, a)                             # tab 2 loses, inside the grace window
    assert_error(r, 401, "REFRESH_RACE")
    # the winner's brand-new cookie must NOT be cleared or revoked
    assert not any(c.startswith(f"{COOKIE}=") for c in set_cookie_headers(r))
    assert refresh_with(client, winner_cookie).status_code == 200


def test_old_token_is_useless_after_rotation_window(client, login, db):
    login()
    a = cookie_of(client)
    refresh_with(client, a)
    sql(db, "UPDATE auth_tokens SET used_at = now() - interval '1 minute' WHERE token_hash = :h",
        h=sha(a))
    assert_error(refresh_with(client, a), 401, "SESSION_EXPIRED")


# ------------------------------------------------------------------ logout
def test_logout_revokes_and_clears(client, login, db):
    login()
    raw = cookie_of(client)
    r = client.post(f"{API}/auth/logout")
    assert r.status_code == 204
    cleared = [c for c in set_cookie_headers(r) if c.startswith(f"{COOKIE}=")]
    assert cleared and "max-age=0" in cleared[0]
    assert db.execute(text("SELECT revoked_at FROM auth_tokens WHERE token_hash = :h"),
                      {"h": sha(raw)}).scalar() is not None
    assert_error(refresh_with(client, raw), 401, "SESSION_EXPIRED")


def test_logout_without_cookie_is_fine(client):
    assert client.post(f"{API}/auth/logout").status_code == 204


def test_logout_only_ends_that_session(client, login):
    login()
    a = cookie_of(client)
    login()
    b = cookie_of(client)
    client.cookies.clear()
    client.post(f"{API}/auth/logout", headers={"Cookie": f"{COOKIE}={a}"})
    assert refresh_with(client, b).status_code == 200


# ------------------------------------------------------------------ forgot password
def test_forgot_password_same_answer_for_known_and_unknown(client, user, db):
    known = client.post(f"{API}/auth/forgot-password", json={"email": user["email"]})
    unknown = client.post(f"{API}/auth/forgot-password", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    n = db.execute(text("SELECT count(*) FROM auth_tokens WHERE type = 'password_reset'")).scalar()
    assert n == 1                                   # only the real account got a token


def test_forgot_password_ignores_deactivated_users(client, user, db):
    sql(db, "UPDATE users SET is_active = false WHERE id = :u", u=user["id"])
    assert client.post(f"{API}/auth/forgot-password", json={"email": user["email"]}).status_code == 200
    assert db.execute(text("SELECT count(*) FROM auth_tokens")).scalar() == 0


def test_forgot_password_token_lasts_30_minutes(client, user, db):
    client.post(f"{API}/auth/forgot-password", json={"email": user["email"]})
    ok = db.execute(text("""
        SELECT expires_at - created_at = interval '30 minutes' FROM auth_tokens
         WHERE type = 'password_reset'""")).scalar()
    assert ok


def test_forgot_password_validates_email(client):
    r = client.post(f"{API}/auth/forgot-password", json={"email": "nope"})
    assert_error(r, 422, "VALIDATION_ERROR")


# ------------------------------------------------------------------ reset password
NEW_PASSWORD = "BrandNewPassw0rd!"


def test_reset_password_happy_path(client, user, reset_token):
    raw = reset_token(user["id"])
    r = client.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD})
    assert r.status_code == 200
    old = client.post(f"{API}/auth/login", json={"email": user["email"], "password": user["password"]})
    new = client.post(f"{API}/auth/login", json={"email": user["email"], "password": NEW_PASSWORD})
    assert old.status_code == 401
    assert new.status_code == 200


def test_reset_token_works_only_once(client, user, reset_token):
    raw = reset_token(user["id"])
    body = {"token": raw, "new_password": NEW_PASSWORD}
    assert client.post(f"{API}/auth/reset-password", json=body).status_code == 200
    assert_error(client.post(f"{API}/auth/reset-password", json=body), 400, "INVALID_TOKEN")


def test_reset_token_expired(client, user, reset_token):
    raw = reset_token(user["id"], minutes=-1)
    r = client.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD})
    assert_error(r, 400, "INVALID_TOKEN")


def test_reset_with_unknown_token(client):
    r = client.post(f"{API}/auth/reset-password",
                    json={"token": "x" * 64, "new_password": NEW_PASSWORD})
    assert_error(r, 400, "INVALID_TOKEN")


def test_reset_weak_password_is_422_and_keeps_token(client, user, reset_token):
    raw = reset_token(user["id"])
    r = client.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": "short"})
    assert_error(r, 422, "VALIDATION_ERROR")
    ok = client.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD})
    assert ok.status_code == 200                    # validation failure must not burn the token


def test_reset_signs_out_every_session(client, login, user, reset_token):
    login()
    stale_cookie = cookie_of(client)
    raw = reset_token(user["id"])
    client.post(f"{API}/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD})
    assert_error(refresh_with(client, stale_cookie), 401, "SESSION_EXPIRED")


def test_refresh_token_cannot_be_used_to_reset_password(client, login):
    login()
    r = client.post(f"{API}/auth/reset-password",
                    json={"token": cookie_of(client), "new_password": NEW_PASSWORD})
    assert_error(r, 400, "INVALID_TOKEN")


def test_reset_token_cannot_be_used_as_refresh_token(client, user, reset_token):
    raw = reset_token(user["id"])
    assert_error(refresh_with(client, raw), 401, "SESSION_EXPIRED")


# ------------------------------------------------------------------ roles
def test_buyer_cannot_reach_admin_route(client, auth_header):
    assert_error(client.get(f"{API}/admin/ping", headers=auth_header), 403, "FORBIDDEN")


def test_admin_route_requires_login(client):
    assert_error(client.get(f"{API}/admin/ping"), 401, "UNAUTHORIZED")


def test_admin_can_reach_admin_route(client, user, make_admin, auth_header):
    make_admin(user["id"])
    r = client.get(f"{API}/admin/ping", headers=auth_header)
    assert r.status_code == 200 and r.json() == {"hello": user["email"]}


def test_role_change_applies_to_an_existing_token(client, user, make_admin, auth_header):
    """Roles are read from the DB on every request, not baked into the JWT."""
    assert client.get(f"{API}/admin/ping", headers=auth_header).status_code == 403
    make_admin(user["id"])
    assert client.get(f"{API}/admin/ping", headers=auth_header).status_code == 200


# ------------------------------------------------------------------ error shape, CORS, health
def test_unknown_route_uses_the_error_shape(client):
    assert_error(client.get(f"{API}/does-not-exist"), 404, "NOT_FOUND")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_preflight_allows_the_frontend_with_credentials(client):
    r = client.options(f"{API}/auth/login", headers={
        "Origin": "http://localhost:4200",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:4200"
    assert r.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_rejects_unknown_origin(client):
    r = client.options(f"{API}/auth/login", headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in r.headers


def test_cors_headers_on_a_real_response(client, user):
    r = client.post(f"{API}/auth/login", headers={"Origin": "http://localhost:4200"},
                    json={"email": user["email"], "password": user["password"]})
    assert r.headers["access-control-allow-origin"] == "http://localhost:4200"
    assert r.headers["access-control-allow-credentials"] == "true"
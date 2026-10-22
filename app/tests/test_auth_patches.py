import re

from sqlalchemy import text

A = "/api/v1/auth"


def test_register_verify_login_never_leaks_refresh_token(client, buyer):
    headers, uid, email = buyer
    r = client.post(f"{A}/login", json={"email": email, "password": "Passw0rd!x"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert "refreshToken" not in body and body["accessToken"]
    assert "refresh_token" in r.cookies                       # cookie is the only carrier
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_verify_response_has_no_refresh_token_in_body(client, caplog):
    email = "v1@x.com"
    with caplog.at_level("WARNING", logger="markethub.auth"):
        vt = client.post(f"{A}/register", json={"full_name": "V One", "email": email, "password": "Passw0rd!x"}).json()["data"]["verificationToken"]
        code = re.search(r"OTP for email \S+: (\d{6})", caplog.text).group(1)
    r = client.post(f"{A}/otp/verify", json={"channel": "email", "code": code}, headers={"Authorization": f"Bearer {vt}"})
    assert r.status_code == 200 and "refreshToken" not in r.json()["data"]
    assert "refresh_token" in r.cookies


def test_login_is_rate_limited_per_ip_and_email(client, buyer):
    _, _, email = buyer
    codes = [client.post(f"{A}/login", json={"email": email, "password": "wrong-pass"}).status_code for _ in range(10)]
    assert codes[:8] == [401] * 8
    assert codes[8] == 429 and codes[9] == 429
    # a different email from the same IP is NOT locked out by the pair limit
    assert client.post(f"{A}/login", json={"email": "other@x.com", "password": "wrong-pass"}).status_code == 401


def test_register_forgot_reset_are_rate_limited(client):
    codes = [client.post(f"{A}/register", json={"full_name": "A B", "email": f"r{i}@x.com", "password": "Passw0rd!x"}).status_code for i in range(12)]
    assert codes.count(429) == 2
    codes = [client.post(f"{A}/forgot-password", json={"email": f"nobody{i}@x.com"}).status_code for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    codes = [client.post(f"{A}/forgot-password", json={"email": "same@x.com"}).status_code for _ in range(4)]
    assert 429 in codes


def test_forgot_password_link_only_logged_in_dev_and_works(client, buyer, caplog):
    _, _, email = buyer
    with caplog.at_level("WARNING", logger="markethub.auth"):
        r = client.post(f"{A}/forgot-password", json={"email": email})
    assert r.status_code == 200
    token = re.search(r"token=(\S+)", caplog.text).group(1)
    assert client.post(f"{A}/reset-password", json={"token": token, "new_password": "N3wPassword!"}).status_code == 200
    assert client.post(f"{A}/login", json={"email": email, "password": "N3wPassword!"}).status_code == 200


def test_swagger_token_route_exists_only_in_dev(client):
    assert client.post(f"{A}/token", data={"username": "a@b.com", "password": "x"}).status_code in (401, 400)  # route present in dev
    # prod: a fresh process with ENV=prod must not register the route
    import subprocess, sys, os
    env = {**os.environ, "ENV": "prod", "COOKIE_SECURE": "true", "OTP_DEV_LOG": "false"}
    code = ("from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "c = TestClient(app)\n"
            "print(c.post('/api/v1/auth/token', data={'username':'a@b.com','password':'x'}).status_code,"
            " c.get('/docs').status_code, c.get('/openapi.json').status_code,"
            " c.post('/api/v1/auth/login', json={'email':'a@b.com','password':'x'}).status_code)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-4:] == ["404", "404", "404", "401"], out.stdout   # token+docs gone, login still there


def test_prod_refuses_dev_otp_logging():
    import subprocess, sys, os
    env = {**os.environ, "ENV": "prod", "COOKIE_SECURE": "true", "OTP_DEV_LOG": "true"}
    out = subprocess.run([sys.executable, "-c", "import app.core.config"], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.returncode != 0 and "OTP_DEV_LOG" in out.stderr


def test_logout_clears_cookie(client, buyer):
    _, _, email = buyer
    client.post(f"{A}/login", json={"email": email, "password": "Passw0rd!x"})
    r = client.post(f"{A}/logout")
    assert r.status_code == 204 and "refresh_token" in r.headers["set-cookie"]

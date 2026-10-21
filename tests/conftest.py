"""Pytest fixtures for the MarketHub API.  Needs a REAL Postgres (not SQLite).

Start a throwaway test database:

    docker run -d --name markethub-test-db -p 5433:5432 \
        -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=markethub_test postgres:16

Run:   pytest -q
Env overrides (all optional):
    TEST_DATABASE_URL   default postgresql+psycopg2://postgres:postgres@localhost:5433/markethub_test
                        (change the driver part to match yours, e.g. postgresql+psycopg://)
    TEST_SCHEMA_SQL     path to one .sql file that builds the schema
    APP_PACKAGE         name of the package that contains main.py (default: app)

Schema source, in this order: TEST_SCHEMA_SQL -> db/ecomm.sql + db/002_buyer_additions.sql
+ db/003_catlog_support.sql -> migrations/*.sql (sorted) -> `alembic upgrade head`.

WARNING: the test database is DROPPED AND RECREATED every run. The URL must contain "test".
"""
import importlib
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5434/markethub_test",
)
APP_PACKAGE = os.environ.get("APP_PACKAGE", "app")
API = "/api/v1"

# Must be set BEFORE the app package is imported: Settings() reads the environment at import time
# and real environment variables win over .env, so a developer's local .env can't leak in here.
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["COOKIE_SECURE"] = "false"
os.environ["CORS_ORIGINS"] = '["http://localhost:4200"]'
os.environ["FRONTEND_URL"] = "http://localhost:4200"

USER = {"full_name": "Test User", "email": "t@x.com", "password": "Passw0rd!x"}


def _import_first(*suffixes: str):
    """Import the first module that exists under the app package (the layout has moved before)."""
    for suffix in suffixes:
        name = f"{APP_PACKAGE}.{suffix}"
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            # only swallow "this candidate does not exist"; a missing third-party package must surface
            if not (e.name and name.startswith(e.name)):
                raise
    raise ImportError(f"None of {[f'{APP_PACKAGE}.{s}' for s in suffixes]} could be imported")


# --------------------------------------------------------------- database
def _run_sql_file(engine, path: Path) -> None:
    # raw DBAPI cursor: no parameter parsing, so ':' and '%' in the SQL are left alone
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


# The project's own schema files, applied in this order. dev_seed.sql is deliberately NOT here:
# it is sample data, and tests create the rows they need.
PROJECT_SCHEMA_FILES = ["db/ecomm.sql", "db/002_buyer_additions.sql", "db/003_catlog_support.sql"]


def _project_schema_files() -> list[Path] | None:
    here = Path(__file__).resolve().parent
    for root in (Path.cwd(), here, here.parent):
        files = [root / f for f in PROJECT_SCHEMA_FILES]
        if all(f.exists() for f in files):
            return files
    return None


def _build_schema(engine) -> None:
    single = os.environ.get("TEST_SCHEMA_SQL")
    if single:
        _run_sql_file(engine, Path(single))
        return
    project_files = _project_schema_files()
    if project_files:
        for f in project_files:
            _run_sql_file(engine, f)
        return
    sql_files = sorted(Path("migrations").glob("*.sql"))
    if sql_files:
        for f in sql_files:
            _run_sql_file(engine, f)
        return
    if Path("alembic.ini").exists():
        subprocess.run(["alembic", "upgrade", "head"], check=True)  # uses DATABASE_URL from env
        return
    pytest.exit("No schema found: set TEST_SCHEMA_SQL, add migrations/*.sql, or add alembic.ini", 2)


@pytest.fixture(scope="session")
def engine():
    db_name = make_url(TEST_DB_URL).database or ""
    if "test" not in db_name:
        pytest.exit(f"Refusing to wipe database '{db_name}': the name must contain 'test'", 2)

    eng = create_engine(TEST_DB_URL, pool_pre_ping=True)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    _build_schema(eng)
    with eng.begin() as c:   # roles are reference data, so they survive the per-test cleanup
        for role in ("buyer", "seller", "admin"):
            c.execute(text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                      {"n": role})
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(engine):
    """Empty the user-related tables after every test. The app commits for real,
    so rolling back a wrapping transaction is not an option."""
    yield
    with engine.begin() as c:
        c.execute(text("TRUNCATE auth_tokens, user_roles, users RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(engine):
    """A session for arranging / inspecting data directly (e.g. aging a token)."""
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()   # releases locks before the TRUNCATE above runs


# --------------------------------------------------------------- app client
@pytest.fixture
def client(engine):
    main = importlib.import_module(f"{APP_PACKAGE}.main")
    db_mod = _import_first("core.database", "db", "database", "core.db")
    make_session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        s = make_session()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[db_mod.get_db] = override_get_db
    with TestClient(main.app, base_url="http://testserver") as c:
        yield c
    main.app.dependency_overrides.clear()


# --------------------------------------------------------------- helpers
@pytest.fixture
def user(client):
    """A registered buyer (t@x.com / Passw0rd!x). Not logged in."""
    r = client.post(f"{API}/auth/register", json=USER)
    assert r.status_code == 201, r.text
    return {**USER, "id": r.json()["id"]}


@pytest.fixture
def login(client, user):
    """Call login(remember=False) -> the response. The refresh cookie lands in client.cookies."""
    def _login(remember: bool = False, email: str | None = None, password: str | None = None):
        return client.post(f"{API}/auth/login", json={
            "email": email or user["email"],
            "password": password or user["password"],
            "remember_me": remember,
        })
    return _login


@pytest.fixture
def auth_header(login):
    """Bearer header for the registered user (logs in as a side effect)."""
    r = login()
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def make_admin(db):
    def _make_admin(user_id: str) -> None:
        db.execute(text("""
            INSERT INTO user_roles (user_id, role_id)
            SELECT :u, id FROM roles WHERE name = 'admin'
        """), {"u": user_id})
        db.commit()
    return _make_admin


@pytest.fixture
def reset_token(db):
    """Insert a password-reset token directly and return the RAW value, so tests never read logs."""
    def _make(user_id: str, minutes: int = 30) -> str:
        sec = _import_first("core.security", "security")
        raw, hashed = sec.new_opaque_token()
        db.execute(text("""
            INSERT INTO auth_tokens (user_id, type, token_hash, expires_at)
            VALUES (:u, 'password_reset', :h, now() + make_interval(mins => :m))
        """), {"u": user_id, "h": hashed, "m": minutes})
        db.commit()
        return raw
    return _make
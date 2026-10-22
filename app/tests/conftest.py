import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@/mh?host=/tmp&port=5433")
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("OTP_PEPPER", "pepper")
os.environ.setdefault("OTP_DEV_LOG", "true")
os.environ.setdefault("ENV", "dev")

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import engine
from app.core.rate_limit import limiter
from app.main import app


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter._hits.clear()
    yield


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    """Autocommit connection: changes are visible to the API immediately."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        yield c


class Catalog:
    """Seeds one approved seller + products through raw SQL."""

    def __init__(self):
        self.seller = uuid.uuid4()
        self.cat = uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,first_name,email,password_hash) VALUES (:i,'S',:e,'x')"),
                      {"i": self.seller, "e": f"s{self.seller.hex[:8]}@x.com"})
            c.execute(text("INSERT INTO seller_profiles(user_id,store_name,store_slug,verification_status) "
                           "VALUES (:i,'S',:s,'approved')"), {"i": self.seller, "s": f"s-{self.seller.hex[:8]}"})
            c.execute(text("INSERT INTO categories(id,name,slug) VALUES (:i,'C',:s)"),
                      {"i": self.cat, "s": f"c-{self.cat.hex[:8]}"})

    def product(self, variants=((90, 10),), status="active", seller=None):
        """variants: iterable of (price, stock). Returns (product_id, [variant_ids])."""
        pid = uuid.uuid4()
        vids = []
        with engine.begin() as c:
            c.execute(text("INSERT INTO products(id,seller_id,category_id,title,slug,status) "
                           "VALUES (:i,:s,:c,'P',:slug,CAST(:st AS product_status))"),
                      {"i": pid, "s": seller or self.seller, "c": self.cat, "slug": f"p-{pid.hex[:10]}", "st": status})
            for n, (price, stock) in enumerate(variants):
                vid = uuid.uuid4()
                vids.append(vid)
                c.execute(text("INSERT INTO product_variants(id,product_id,sku,mrp,price,stock_qty) "
                               "VALUES (:i,:p,:sku,:mrp,:pr,:st)"),
                          {"i": vid, "p": pid, "sku": f"sku-{vid.hex[:10]}", "mrp": price + 10, "pr": price, "st": stock})
        return pid, vids


@pytest.fixture()
def catalog():
    return Catalog()


@pytest.fixture()
def buyer(client, caplog):
    """A registered + verified buyer. Returns (headers, user_id, email)."""
    email = f"b{uuid.uuid4().hex[:8]}@x.com"
    with caplog.at_level("WARNING", logger="markethub.auth"):
        r = client.post("/api/v1/auth/register", json={"full_name": "Test Buyer", "email": email, "password": "Passw0rd!x"})
        assert r.status_code == 201, r.text
        vt = r.json()["data"]["verificationToken"]
        code = re.search(r"OTP for email \S+: (\d{6})", caplog.text).group(1)
    r = client.post("/api/v1/auth/otp/verify", json={"channel": "email", "code": code},
                    headers={"Authorization": f"Bearer {vt}"})
    assert r.status_code == 200, r.text
    tok = r.json()["data"]["accessToken"]
    uid = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["data"]["id"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {tok}"}, uid, email

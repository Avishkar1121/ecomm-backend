"""Create dev accounts with KNOWN passwords so the frontend can log in on day one.

Run from the project root (venv active), AFTER the SQL migrations (and dev_seed.sql if you use it):

    python scripts/seed_dev_users.py
    python scripts/seed_dev_users.py --password "SomethingElse1!"     # or env SEED_PASSWORD

Safe to run repeatedly: existing accounts are updated (password reset to the value below),
nothing is duplicated. DEV ONLY: these passwords are public knowledge.

Creates:   buyer@markethub.dev    role buyer                      (+ one default address)
           seller@markethub.dev   roles buyer + seller            (+ approved store profile)
           admin@markethub.dev    role admin
Also:      gives the users made by dev_seed.sql (seed.*@markethub.dev) a real password, so they can log in too.
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # project root, so `import app` works
os.chdir(ROOT)                  # so Settings() finds .env wherever the script is launched from

from sqlalchemy import create_engine, text  # noqa: E402

DEFAULT_PASSWORD = "Passw0rd!dev"
SEED_PLACEHOLDER_HASH = "seed-not-a-real-hash"   # what dev_seed.sql wrote into password_hash


def _load(attr: str, *modules: str):
    """Return `attr` from the first module that exists (the project layout has moved before)."""
    for name in modules:
        try:
            return getattr(importlib.import_module(name), attr)
        except ModuleNotFoundError as e:
            if not (e.name and name.startswith(e.name)):
                raise            # a missing third-party package should surface, not be swallowed
        except AttributeError:
            continue
    raise ImportError(f"Could not find {attr!r} in any of: {', '.join(modules)}")


settings = _load("settings", "app.core.config", "app.config")
hash_password = _load("hash_password", "app.core.security", "app.security")

#            email                    first    last      roles
ACCOUNTS = [
    ("buyer@markethub.dev",  "Demo", "Buyer",  ["buyer"]),
    ("seller@markethub.dev", "Demo", "Seller", ["buyer", "seller"]),
    ("admin@markethub.dev",  "Demo", "Admin",  ["admin"]),
]


def upsert_user(conn, email, first, last, pw_hash) -> tuple[str, str]:
    row = conn.execute(text("SELECT id FROM users WHERE email = :e AND deleted_at IS NULL"),
                       {"e": email}).first()
    if row:
        conn.execute(text("""
            UPDATE users SET password_hash = :h, first_name = :f, last_name = :l, is_active = true
             WHERE id = :id
        """), {"h": pw_hash, "f": first, "l": last, "id": row[0]})
        return str(row[0]), "updated"
    uid = conn.execute(text("""
        INSERT INTO users (first_name, last_name, email, password_hash)
        VALUES (:f, :l, :e, :h) RETURNING id
    """), {"f": first, "l": last, "e": email, "h": pw_hash}).scalar_one()
    return str(uid), "created"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--password", default=os.environ.get("SEED_PASSWORD", DEFAULT_PASSWORD))
    args = parser.parse_args()
    if len(args.password) < 8:
        sys.exit("Password must be at least 8 characters (same rule as registration).")

    engine = create_engine(settings.database_url)
    db_name = (engine.url.database or "").lower()
    if "prod" in db_name:
        sys.exit(f"Refusing to seed a database named '{db_name}'. Dev accounts have public passwords.")
    print(f"Target database: {engine.url.render_as_string(hide_password=True)}")

    pw_hash = hash_password(args.password)      # hash once; same password for every dev account
    report = []
    with engine.begin() as conn:                # one transaction: all or nothing
        for email, first, last, roles in ACCOUNTS:
            uid, action = upsert_user(conn, email, first, last, pw_hash)
            conn.execute(text("""
                INSERT INTO user_roles (user_id, role_id)
                SELECT :u, id FROM roles WHERE name = ANY(:names)
                ON CONFLICT DO NOTHING
            """), {"u": uid, "names": roles})
            if "seller" in roles:
                conn.execute(text("""
                    INSERT INTO seller_profiles (user_id, store_name, store_slug, description,
                                                 verification_status)
                    VALUES (:u, 'Demo Store', 'demo-store', 'Dev test store.', 'approved')
                    ON CONFLICT (user_id) DO UPDATE SET verification_status = 'approved'
                """), {"u": uid})
            if email == "buyer@markethub.dev":
                has_address = conn.execute(text(
                    "SELECT 1 FROM addresses WHERE user_id = :u AND deleted_at IS NULL LIMIT 1"),
                    {"u": uid}).first()
                if not has_address:
                    conn.execute(text("""
                        INSERT INTO addresses (user_id, label, full_name, phone, line1, city,
                                               state_code, zip, is_default)
                        VALUES (:u, 'Home', 'Demo Buyer', '9876500001', '12, Sample Residency, MG Road',
                                'Mumbai', 'MH', '400001', true)
                    """), {"u": uid})
            report.append((email, "+".join(roles), action))

        # users created by dev_seed.sql carry a placeholder hash, so nobody could log in as them
        fixed = conn.execute(text(
            "UPDATE users SET password_hash = :h WHERE password_hash = :ph"),
            {"h": pw_hash, "ph": SEED_PLACEHOLDER_HASH}).rowcount

    print(f"\n{'email':<28}{'roles':<16}result")
    for email, roles, action in report:
        print(f"{email:<28}{roles:<16}{action}")
    if fixed:
        print(f"\nAlso gave a real password to {fixed} user(s) from dev_seed.sql (seed.*@markethub.dev).")
    print(f"\nPassword for all of them: {args.password}")


if __name__ == "__main__":
    main()
"""Demo seed (P5-T2): sign-in accounts and an integration API key.

Run from backend/ with DATABASE_URL set:

    python -m app.seed

Idempotent — safe to run on every start (scripts/demo.sh does). The API key's
plaintext is printed only when it is first created; keys are stored hashed.

These credentials are for local demos only. The seed refuses any non-SQLite
DATABASE_URL unless ENTITYIQ_ALLOW_DEMO_SEED=1 is set, because it creates
accounts with a publicly documented password.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.auth.operator import hash_password
from app.auth.service import create_api_client
from app.models.api_client import ApiClient
from app.models.operator import Operator

DEMO_PASSWORD = "entityiq-demo"
DEMO_ACCOUNTS: list[tuple[str, str, str]] = [
    ("operator@demo.entityiq.dev", "Demo Operator", "operator"),
    ("lead@demo.entityiq.dev", "Demo Lead", "lead"),
]
DEMO_API_CLIENT = "demo-integration"


class UnsafeSeedTarget(RuntimeError):
    """Raised when the seed would write demo credentials to a non-local DB."""


def check_seed_target(database_url: str) -> None:
    if database_url.startswith("sqlite"):
        return
    if os.environ.get("ENTITYIQ_ALLOW_DEMO_SEED") == "1":
        return
    raise UnsafeSeedTarget(
        "Refusing to seed demo accounts (public password) into a non-SQLite "
        "database. Set ENTITYIQ_ALLOW_DEMO_SEED=1 if this really is a throwaway DB."
    )


@dataclass
class SeedResult:
    created_accounts: list[str] = field(default_factory=list)
    api_key: str | None = None  # plaintext, only when newly created


def seed(db: Session) -> SeedResult:
    result = SeedResult()
    for email, full_name, role in DEMO_ACCOUNTS:
        if db.query(Operator).filter(Operator.email == email).first():
            continue
        db.add(
            Operator(
                email=email,
                full_name=full_name,
                role=role,
                password_hash=hash_password(DEMO_PASSWORD),
            )
        )
        result.created_accounts.append(email)
    db.commit()

    if not db.query(ApiClient).filter(ApiClient.name == DEMO_API_CLIENT).first():
        _client, key = create_api_client(DEMO_API_CLIENT, db)
        result.api_key = key
    return result


def main() -> None:
    from app.db.session import DATABASE_URL, SessionLocal  # noqa: PLC0415

    check_seed_target(DATABASE_URL)
    db = SessionLocal()
    try:
        result = seed(db)
    finally:
        db.close()

    print("Demo sign-in (password for both):", DEMO_PASSWORD)
    for email, _name, role in DEMO_ACCOUNTS:
        print(f"  {role:<8} {email}")
    if result.api_key:
        print(f"Integration API key (shown once): {result.api_key}")


if __name__ == "__main__":
    main()

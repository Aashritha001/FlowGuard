"""First-run setup: creates the tables, the first Super Admin and the integration status list. Holds no business data.

    python -m app.setup                      # prints a generated password for 'admin' once
    FLOWGUARD_ADMIN_PASSWORD=... python -m app.setup

The server runs the same bootstrap on start-up if the database has no users yet.
"""
import secrets
import string
import sys

from sqlalchemy import select, func

from .config import settings
from .db import Base, engine, SessionLocal, install_append_only_guards, ensure_columns
from .models import User, Integration, Setting
from .security import hash_password
from .services.audit import log

INTEGRATIONS = [
    ("GSC workbook (.xlsx) upload", "CSV", "WORKING", "UPLOAD", "Jobs, agent tasks, agents and rates from the GSC workbook."),
    ("CSV upload", "CSV", "WORKING", "UPLOAD", "Quarantine, column mapping, validation and Shadow Mode."),
    ("GSC REST API", "REST_API", "INTEGRATION_READY", "READ_ONLY", "Adapter interface defined; no endpoint configured."),
    ("PostgreSQL (read replica)", "POSTGRESQL", "INTEGRATION_READY", "READ_ONLY", "SQLAlchemy connector; not connected."),
    ("MySQL", "MYSQL", "INTEGRATION_READY", "READ_ONLY", "Adapter interface; not connected."),
    ("Microsoft SQL Server", "MSSQL", "INTEGRATION_READY", "READ_ONLY", "Adapter interface; not connected."),
    ("Sage", "SAGE", "EXPORT_ONLY", "EXPORT_ONLY", "Sage-ready CSV export with configurable field mapping. No live Sage API."),
]


def generate_password(n: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def bootstrap(db, password: str | None = None) -> str | None:
    """Idempotent. Returns the admin password only when it generated a new admin account."""
    created = None
    if not db.scalar(select(func.count()).select_from(User)):
        pw = password or settings.admin_password or generate_password()
        db.add(User(username="admin", display_name="Administrator", role="SUPER_ADMIN", password_hash=hash_password(pw)))
        log(db, "system", "USER_CREATED", "user", "admin", new="SUPER_ADMIN", reason="First-run setup")
        created = pw
    if not db.scalar(select(func.count()).select_from(Integration)):
        for name, kind, status, mode, desc in INTEGRATIONS:
            db.add(Integration(name=name, kind=kind, status=status, access_mode=mode, description=desc))
    db.commit()
    return created


def load_business_date(db) -> None:
    if settings.business_date_env:
        settings.business_date = settings.business_date_env
        return
    st = db.get(Setting, "business_date")
    settings.business_date = (st.value or {}).get("date", "") if st else ""


def main():
    Base.metadata.create_all(engine)
    ensure_columns(engine)
    install_append_only_guards(engine)
    with SessionLocal() as db:
        pw = bootstrap(db)
    if pw:
        print("Created Super Admin 'admin'." + ("" if settings.admin_password else f" Password: {pw}"))
        print("Sign in, then add your team under Settings > Users. Change this password under Settings > Your account.")
    else:
        print("Database already has users; nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

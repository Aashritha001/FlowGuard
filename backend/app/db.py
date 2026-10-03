"""Database plumbing. Portable between SQLite (prototype) and PostgreSQL (production)."""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import create_engine, event, String, Numeric
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .config import settings

PENNY = Decimal("0.01")


def money(v) -> Decimal:
    """Coerce to a 2dp Decimal. Refuses floats so binary floating point can never enter the money path."""
    if v is None:
        return None
    if isinstance(v, float):
        raise TypeError("Floats are not allowed for money; pass str or Decimal")
    return Decimal(str(v)).quantize(PENNY, rounding=ROUND_HALF_UP)


class Money(TypeDecorator):
    """NUMERIC(14,2) on PostgreSQL; exact decimal string on SQLite (which has no native decimal)."""
    impl = String(24)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(14, 2, asdecimal=True))
        return dialect.type_descriptor(String(24))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = money(value)
        return d if dialect.name == "postgresql" else str(d)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return money(value)


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    else:  # serverless hosts drop idle connections: check before use and keep the pool small
        kw.update(pool_pre_ping=True, pool_size=2, max_overflow=3, pool_recycle=300)
    eng = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def install_append_only_guards(eng) -> None:
    """Audit and AI-interaction tables are append-only at the database level, not just in code."""
    with eng.begin() as c:
        if eng.dialect.name == "sqlite":
            for t in ("audit_events", "ai_interactions"):
                c.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
                    BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;""")
                c.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
                    BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;""")
        elif eng.dialect.name == "postgresql":
            c.exec_driver_sql("""CREATE OR REPLACE FUNCTION fg_append_only() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'append-only table'; END; $$ LANGUAGE plpgsql;""")
            for t in ("audit_events", "ai_interactions"):
                c.exec_driver_sql(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
                c.exec_driver_sql(f"""CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
                    FOR EACH ROW EXECUTE FUNCTION fg_append_only();""")


# Additive, nullable columns introduced after a database may already exist. create_all() never alters
# existing tables, so these are added in place. Never drops or rewrites anything.
ADDED_COLUMNS = {
    "agents": {"paid_via_agent_id": "INTEGER REFERENCES agents(id)", "vat_registered_from": "DATE"},
    "pricing_rules": {"agent_code": "VARCHAR(16)", "included_minutes": "INTEGER", "agent_extra_per_30": "VARCHAR(24)",
                      "included_units": "INTEGER"},
    "jobs": {"minutes_on_site": "INTEGER", "attributes": "JSON"},
}


def ensure_columns(eng) -> None:
    from sqlalchemy import inspect
    insp = inspect(eng)
    with eng.begin() as c:
        for table, cols in ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {col["name"] for col in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    if eng.dialect.name == "postgresql" and name == "agent_extra_per_30":
                        ddl = "NUMERIC(14,2)"
                    c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

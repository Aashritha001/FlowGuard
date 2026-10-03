import os
import sys

os.environ.setdefault("FLOWGUARD_PBKDF2_ITERATIONS", "1000")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine, event

from app.db import Base, install_append_only_guards
from app.config import settings
from tests import demo_fixture as seedmod

settings.business_date = "2026-10-03"  # the synthetic fixture tells its story around this date


def _engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        c.execute("PRAGMA foreign_keys=ON")
    return eng


@pytest.fixture(scope="session")
def seeded_template():
    """Seed once; individual tests get their own copy so they cannot affect each other."""
    eng = _engine()
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as db:
        seedmod.seed(db, iterations=1000, small=True)
    install_append_only_guards(eng)
    return eng


@pytest.fixture()
def db(seeded_template):
    eng = _engine()
    raw_src = seeded_template.raw_connection()
    raw_dst = eng.raw_connection()
    raw_src.driver_connection.backup(raw_dst.driver_connection)
    raw_dst.close()
    raw_src.close()
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def find_workbook():
    """The real GSC workbook is confidential and never committed. Tests that need it look for
    FLOWGUARD_TEST_WORKBOOK, then any GSC*DataPack*.xlsx in the project root or data/, and skip if none."""
    from pathlib import Path
    env = os.environ.get("FLOWGUARD_TEST_WORKBOOK")
    if env and Path(env).exists():
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    for folder in (root / "data", root):
        hits = sorted(folder.glob("GSC*DataPack*.xlsx"))
        if hits:
            return hits[0]
    return None

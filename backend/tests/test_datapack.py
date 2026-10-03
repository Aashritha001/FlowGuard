"""End-to-end check against the GSC data pack: the engine must never be READY where the independent reference is not,
READY amounts must match to the penny, and reconciliation/reconstruction must be clean."""
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.config import settings
from tests.conftest import _engine, find_workbook

XLSX = find_workbook()


@pytest.mark.skipif(XLSX is None, reason="confidential data pack not present (set FLOWGUARD_TEST_WORKBOOK)")
def test_datapack_matches_reference():
    pytest.importorskip("openpyxl")
    from app import datapack
    old_today = settings.business_date
    eng = _engine()
    Base.metadata.create_all(eng)
    try:
        pack = datapack.read_pack(XLSX)
        with sessionmaker(bind=eng, expire_on_commit=False)() as db:
            datapack.load(db, pack)
            res = datapack.run_pipeline(db)
            cmp_ = datapack.compare(db, pack, datapack.reference(pack, datapack.categories(db)))
    finally:
        settings.business_date = old_today
    assert cmp_["wrong"] == []
    assert cmp_["invoice_differences"] == []
    assert cmp_["categories"].get("MATCH_READY", 0) > 1500
    assert res["reconciliation"]["mismatch_count"] == 0
    assert set(res["reconstruction"]) == {"REPRODUCED SUCCESSFULLY"}
    # held tasks may only be held for review-type reasons, never silently dropped
    assert all(h["status"] in ("NEEDS_REVIEW", "BLOCKED") and h["reasons"] for h in cmp_["held"])

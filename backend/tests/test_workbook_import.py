"""Workbook import as used by the web app: idempotent re-import, source updates, frozen invoiced jobs, and
rate differences reported but never applied."""
import copy
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import Job, PricingRule, User
from app.services import invoicing
from tests.conftest import _engine, find_workbook

XLSX = find_workbook()
pytestmark = pytest.mark.skipif(XLSX is None, reason="confidential data pack not present (set FLOWGUARD_TEST_WORKBOOK)")


@pytest.fixture()
def fresh():
    pytest.importorskip("openpyxl")
    from app import workbook, datapack
    old = settings.business_date
    pack = workbook.read_pack(XLSX.read_bytes())
    datapack.set_period(pack)
    settings.business_date = datapack.PAYRUN.isoformat()
    eng = _engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    yield db, workbook, pack
    db.close()
    settings.business_date = old


def test_reimport_is_idempotent_and_applies_source_changes(fresh):
    db, wb, pack = fresh
    first = wb.import_workbook(db, "admin", pack, "jan.xlsx")
    assert first["tasks_added"] == len(pack.tasks) and first["rate_differences"] == []
    assert db.scalar(select(func.count()).select_from(User)) == 0  # an import never creates logins
    rules = db.scalar(select(func.count()).select_from(PricingRule))

    again = wb.import_workbook(db, "admin", pack, "jan.xlsx")
    assert (again["tasks_added"], again["tasks_updated"], again["tasks_unchanged"]) == (0, 0, len(pack.tasks))
    assert db.scalar(select(func.count()).select_from(PricingRule)) == rules

    # a task that was pending is now verified at source -> updated, not duplicated
    later = copy.deepcopy(pack)
    t = next(x for x in later.tasks if x["Verification"] == "Pending")
    from app import datapack
    t["Verification"], t["Verification Date"] = "Verified", f"{datapack.PAYRUN.replace(day=10):%d/%m/%Y}"
    res = wb.import_workbook(db, "admin", later, "feb.xlsx")
    assert res["tasks_updated"] == 1 and res["tasks_added"] == 0
    assert db.scalar(select(Job).where(Job.external_job_id == t["Agent Job Ref"])).verification_status == "VERIFIED"


def test_invoiced_tasks_are_frozen_and_rate_changes_are_only_reported(fresh):
    db, wb, pack = fresh
    wb.import_workbook(db, "admin", pack, "jan.xlsx")
    ready = [j.id for j, _ in invoicing.eligible(db)][:5]
    invoicing.generate_for_jobs(db, "finance", ready)
    db.commit()
    inv_job = db.get(Job, ready[0])

    later = copy.deepcopy(pack)
    t = next(x for x in later.tasks if x["Agent Job Ref"] == inv_job.external_job_id)
    t["Time On Site (mins)"] = (t["Time On Site (mins)"] or 0) + 90
    cat = next(iter(later.client_prices))
    later.client_prices[cat] = later.client_prices[cat] + Decimal("5.00")
    res = wb.import_workbook(db, "admin", later, "feb.xlsx")

    assert [x["task"] for x in res["changed_after_invoicing"]] == [inv_job.external_job_id]
    db.refresh(inv_job)
    assert inv_job.minutes_on_site != t["Time On Site (mins)"]  # invoiced facts never change
    assert res["rate_differences"] and all(d["in_force"] for d in res["rate_differences"])
    rule = db.scalar(select(PricingRule).where(PricingRule.kind == "BASE", PricingRule.client_amount == later.client_prices[cat]))
    assert rule is None  # prices were not changed by the import


def test_rejects_files_that_are_not_workbooks(fresh):
    _, wb, _ = fresh
    with pytest.raises(wb.WorkbookError):
        wb.read_pack(b"this is not a spreadsheet")
    with pytest.raises(wb.WorkbookError):
        wb.read_pack(b"PK\x03\x04 broken zip")


def test_csv_form_imports_the_same_as_the_workbook(fresh, tmp_path):
    """The single-file CSV made by tools/workbook_to_csv.py carries every pricing fact the workbook does."""
    import importlib.util
    db, wb, pack = fresh
    tool = Path(__file__).resolve().parents[2] / "tools" / "workbook_to_csv.py"
    spec = importlib.util.spec_from_file_location("workbook_to_csv", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "pack.csv"
    mod.convert(XLSX, out)
    csv_pack = wb.read_pack(out.read_bytes())
    for f in ("tasks", "agents", "clients", "client_prices", "std_rates", "special_rates", "company", "numbering", "vat_rate"):
        assert getattr(csv_pack, f) == getattr(pack, f), f
    a = wb.import_workbook(db, "admin", csv_pack, "pack.csv")
    assert a["tasks_added"] == len(pack.tasks)
    assert wb.import_workbook(db, "admin", pack, "pack.xlsx")["tasks_updated"] == 0  # same facts either way


def test_csv_that_is_not_a_data_pack_is_rejected(fresh):
    _, wb, _ = fresh
    with pytest.raises(wb.WorkbookError):
        wb.read_pack(b"job_id,client\n1,A\n")

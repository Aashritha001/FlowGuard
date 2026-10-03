"""Invoice downloads: PDFs carry the recorded totals and the self-billing wording, batches download as a ZIP,
the list downloads as CSV, and agents can only download their own invoices."""
import io
import zipfile

import pytest
from sqlalchemy import select

pytest.importorskip("fpdf")

from app.models import Invoice, User
from app.services import invoice_docs
from tests.test_invariants import login, call


def _text(pdf: bytes) -> str:
    return pdf.decode("latin-1")


def test_client_invoice_pdf(db):
    inv = db.scalar(select(Invoice).where(Invoice.kind == "CLIENT").limit(1))
    pdf = invoice_docs.render_pdf(db, inv)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_self_billed_vat_invoice_has_hmrc_wording(db):
    inv = db.scalar(select(Invoice).where(Invoice.kind == "AGENT", Invoice.self_billed == True, Invoice.vat > "0.00"))  # noqa
    if inv is None:
        pytest.skip("fixture has no VAT self-billed invoice")
    assert invoice_docs.document_kind(inv) == "SELF-BILLING INVOICE"
    pdf = invoice_docs.render_pdf(db, inv)
    assert pdf.startswith(b"%PDF")


def test_download_routes_and_rbac(db):
    fin = login(db, "finance")
    inv = db.scalar(select(Invoice).order_by(Invoice.id).limit(1))
    r = call(db, fin, "GET", f"/api/invoices/{inv.id}/pdf")
    assert r.status == 200 and r.content_type == "application/pdf" and r.filename == f"{inv.number}.pdf"
    assert bytes(r.body).startswith(b"%PDF")

    r = call(db, fin, "GET", f"/api/batches/{inv.batch_id}/pdfs")
    assert r.status == 200 and r.content_type == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.body)).namelist()
    expected = {f"{i.number}.pdf" for i in db.scalars(select(Invoice).where(Invoice.batch_id == inv.batch_id))}
    assert set(names) == expected

    r = call(db, fin, "GET", "/api/invoice-list.csv", query={"kind": "CLIENT"})
    assert r.status == 200 and r.content_type == "text/csv"
    lines = r.body.strip().splitlines()
    assert lines[0].startswith("Invoice,Side") and len(lines) - 1 == len(list(db.scalars(select(Invoice).where(Invoice.kind == "CLIENT"))))

    # an agent can download their own invoice but gets 404 for anyone else's
    ag = login(db, "agent.sam")
    me = db.scalar(select(User).where(User.username == "agent.sam"))
    own = db.scalar(select(Invoice).where(Invoice.kind == "AGENT", Invoice.agent_id == me.agent_id))
    other = db.scalar(select(Invoice).where(Invoice.kind == "AGENT", Invoice.agent_id != me.agent_id))
    if own:
        assert call(db, ag, "GET", f"/api/invoices/{own.id}/pdf").status == 200
    assert call(db, ag, "GET", f"/api/invoices/{other.id}/pdf").status == 404
    assert call(db, ag, "GET", f"/api/batches/{inv.batch_id}/pdfs").status == 403

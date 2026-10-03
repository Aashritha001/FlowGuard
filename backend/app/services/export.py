"""Sage-ready export with a configurable accounting field mapping. The real GSC Sage schema can be
configured later in the mapping without touching the financial engine."""
import csv
import io
from datetime import datetime

from sqlalchemy import select

from ..models import now as _now
from ..models import Invoice, Client, Agent, Setting
from .audit import log

DEFAULT_MAPPING = [  # (output column, source expression)
    ["Type", "type_code"], ["Account Reference", "account_ref"], ["Nominal A/C Ref", "nominal"],
    ["Date", "issue_date"], ["Reference", "number"], ["Details", "details"], ["Net Amount", "net"],
    ["Tax Code", "tax_code"], ["Tax Amount", "vat"], ["Gross", "gross"], ["PO Reference", "po_refs"],
]
NOMINALS = {"CLIENT": "4000", "AGENT": "5000"}  # default nominal codes; adjust in the export mapping
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def escape_cell(v) -> str:
    """CSV/formula-injection protection for text cells (OWASP)."""
    s = "" if v is None else str(v)
    if s.startswith(DANGEROUS):
        return "'" + s
    return s


def mapping(db) -> list:
    s = db.get(Setting, "sage_mapping")
    return s.value["columns"] if s else DEFAULT_MAPPING


def _source(inv: Invoice, clients, agents) -> dict:
    party = clients.get(inv.client_id) if inv.kind == "CLIENT" else agents.get(inv.agent_id)
    return {"type_code": "SI" if inv.kind == "CLIENT" else "PI",
            "account_ref": (party.sage_account_ref if inv.kind == "CLIENT" else party.sage_supplier_ref) if party else "",
            "nominal": NOMINALS[inv.kind], "issue_date": inv.issue_date.strftime("%d/%m/%Y"), "number": inv.number,
            "details": f"{party.name if party else ''} {inv.number}".strip(), "net": f"{inv.net:.2f}",
            "tax_code": "T1" if inv.vat > 0 else "T9", "vat": f"{inv.vat:.2f}", "gross": f"{inv.gross:.2f}",
            "po_refs": " ".join(inv.po_refs or [])}


NUMERIC = {"net", "vat", "gross"}


def build_csv(db, actor: str, batch_id=None, mark_exported=True) -> str:
    clients = {c.id: c for c in db.scalars(select(Client))}
    agents = {a.id: a for a in db.scalars(select(Agent))}
    qy = select(Invoice).order_by(Invoice.id)
    if batch_id:
        qy = qy.where(Invoice.batch_id == batch_id)
    invs = list(db.scalars(qy))
    cols = mapping(db)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow([c[0] for c in cols])
    for inv in invs:
        src = _source(inv, clients, agents)
        w.writerow([src.get(expr, "") if expr in NUMERIC else escape_cell(src.get(expr, "")) for _, expr in cols])
        if mark_exported and not inv.exported_at:
            inv.exported_at = _now()
    log(db, actor, "INVOICE_EXPORT", "export", "sage", new=f"{len(invs)} invoices",
        details={"format": "Sage-ready CSV", "columns": [c[0] for c in cols]})
    db.commit()
    return buf.getvalue()

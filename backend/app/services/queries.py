"""Known-safe, parameterised read queries shared by the API and the AI tool gateway.
Agent scoping is enforced here, server-side, from the authenticated user - never from request input."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func, exists, or_, and_, asc, desc

from ..models import (Job, JobDecision, DecisionReason, Client, Agent, Invoice, InvoiceLine, ExceptionCase,
                      ReviewDecision, RateCardVersion, Contract, VATConfiguration, AuditEvent)
from ..engine import reason_codes as rc
from . import snapshot as snapmod


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def _d(v):
    return None if v is None else str(v)


def _client_map(db):
    return {c.id: c for c in db.scalars(select(Client))}


def _agent_map(db):
    return {a.id: a for a in db.scalars(select(Agent))}


def resolve_client(db, v):
    if v in (None, ""):
        return None
    if isinstance(v, int) or str(v).isdigit():
        return int(v)
    s = str(v).strip().lower()
    for c in db.scalars(select(Client)):
        if c.code.lower() == s or c.name.lower() == s or s in c.name.lower():
            return c.id
        if s.startswith("client ") and c.code.lower() == "cl" + s.split()[-1]:
            return c.id
    return -1


def resolve_agent(db, v):
    if v in (None, ""):
        return None
    if isinstance(v, int) or str(v).isdigit():
        return int(v)
    s = str(v).strip().lower()
    for a in db.scalars(select(Agent)):
        if a.code.lower() == s or s in a.name.lower():
            return a.id
    return -1


SORTS = {"id": Job.id, "job_date": Job.job_date, "external_job_id": Job.external_job_id, "job_type": Job.job_type,
         "client_net": JobDecision.client_net, "status": JobDecision.status, "updated": Job.updated_at}


def search_jobs(db, user, f: dict) -> dict:
    qy = (select(Job, JobDecision).outerjoin(JobDecision, and_(JobDecision.job_id == Job.id, JobDecision.is_current == True))  # noqa
          .where(Job.shadow == False))  # noqa
    if user.role == "AGENT":
        qy = qy.where(Job.agent_id == user.agent_id)  # server-side scoping; request cannot widen it
    elif f.get("agent"):
        qy = qy.where(Job.agent_id == resolve_agent(db, f["agent"]))
    if f.get("client"):
        qy = qy.where(Job.client_id == resolve_client(db, f["client"]))
    if f.get("job_type"):
        qy = qy.where(Job.job_type == str(f["job_type"]).upper())
    if f.get("verification"):
        qy = qy.where(Job.verification_status == str(f["verification"]).upper())
    if f.get("status"):
        sts = [s.strip().upper() for s in str(f["status"]).split(",")]
        qy = qy.where(JobDecision.status.in_(sts))
    if f.get("invoiced") in ("yes", True, "true"):
        qy = qy.where(JobDecision.frozen == True)  # noqa
    elif f.get("invoiced") in ("no", False, "false"):
        qy = qy.where(JobDecision.frozen == False)  # noqa
    if f.get("reason"):
        codes = [c.strip().upper() for c in str(f["reason"]).split(",")]
        qy = qy.where(exists().where(DecisionReason.decision_id == JobDecision.id, DecisionReason.code.in_(codes)))
    if f.get("bucket"):
        from .analytics import bucket_of
        if f["bucket"] == "verified_not_invoiced":
            qy = qy.where(JobDecision.status == "READY", JobDecision.frozen == False)  # noqa
        else:
            codes = [c for c in rc.CATALOGUE if bucket_of(c) == f["bucket"]]
            qy = qy.where(Job.verification_status == "VERIFIED", exists().where(
                ExceptionCase.job_id == Job.id, ExceptionCase.state == "OPEN", ExceptionCase.reason_code.in_(codes)))
    for k, col in (("date_from", Job.job_date), ("date_to", Job.job_date)):
        if f.get(k):
            try:
                d = date.fromisoformat(str(f[k]))
            except ValueError:
                raise ValueError(f"{k} must be YYYY-MM-DD")
            qy = qy.where(col >= d if k == "date_from" else col <= d)
    for flag in ("weekend", "emergency", "revisit"):
        if f.get(flag) in (True, "true", "yes", "1"):
            qy = qy.where(getattr(Job, flag) == True)  # noqa
    if f.get("scenario"):
        qy = qy.where(Job.demo_scenario.ilike(f"%{str(f['scenario'])[:40]}%"))
    if f.get("q"):
        s = f"%{str(f['q']).strip()[:40]}%"
        digits = "".join(ch for ch in str(f["q"]) if ch.isdigit())
        conds = [Job.external_job_id.ilike(s), Job.postcode.ilike(s), Job.po_number.ilike(s)]
        if digits:
            conds.append(Job.id == int(digits))
        qy = qy.where(or_(*conds))
    total = db.scalar(select(func.count()).select_from(qy.subquery()))
    sort = SORTS.get(f.get("sort") or "id", Job.id)
    qy = qy.order_by(desc(sort) if f.get("dir", "desc") == "desc" else asc(sort), desc(Job.id))
    page = max(1, int(f.get("page") or 1))
    size = min(200, max(1, int(f.get("page_size") or 25)))
    rows = list(db.execute(qy.offset((page - 1) * size).limit(size)))
    cm, am = _client_map(db), _agent_map(db)
    return {"total": total, "page": page, "page_size": size,
            "rows": [job_row(db, j, d, cm, am, agent_view=user.role == "AGENT") for j, d in rows]}


def _invoice_numbers(db, job_id):
    return {k: n for k, n in db.execute(select(InvoiceLine.kind, Invoice.number).join(Invoice).where(InvoiceLine.job_id == job_id))}


def job_row(db, j: Job, d: JobDecision | None, cm, am, agent_view=False) -> dict:
    invs = _invoice_numbers(db, j.id) if d and d.frozen else {}
    row = {"id": j.id, "ref": snapmod.job_ref(j), "external_job_id": j.external_job_id,
           "client": cm[j.client_id].name if j.client_id in cm else None,
           "client_code": cm[j.client_id].code if j.client_id in cm else None,
           "agent": am[j.agent_id].name if j.agent_id in am else None, "agent_code": am[j.agent_id].code if j.agent_id in am else None,
           "job_type": j.job_type, "job_date": _d(j.job_date), "verification_status": j.verification_status,
           "status": d.status if d else None, "pricing_status": ("PRICED" if d and d.client_net is not None else "UNPRICED"),
           "invoiced": bool(d and d.frozen), "invoice_status": "INVOICED" if d and d.frozen else ("AWAITING" if d and d.status == "READY" else "NOT_ELIGIBLE"),
           "client_invoice": invs.get("CLIENT"), "agent_invoice": invs.get("AGENT"),
           "agent_net": _d(d.agent_net) if d else None, "po_number": j.po_number,
           "weekend": j.weekend, "emergency": j.emergency, "revisit": j.revisit,
           "reasons": [r.code for r in d.reasons] if d else [], "created_at": _d(j.created_at), "updated_at": _d(j.updated_at)}
    if not agent_view:
        row.update({"client_net": _d(d.client_net) if d else None, "value_at_stake": _d(d.value_at_stake) if d else None,
                    "postcode": j.postcode, "scenario": j.demo_scenario})
    else:
        row["client"] = row["client"]  # agents may see which client the job was for, not the price
    return row


def get_job(db, user, job_id: int) -> Job:
    j = db.get(Job, job_id)
    if not j or j.shadow:
        raise NotFound("Job not found")
    if user.role == "AGENT" and j.agent_id != user.agent_id:
        raise NotFound("Job not found")  # same response as missing: no information leak about other agents
    return j


def job_detail(db, user, job_id: int) -> dict:
    j = get_job(db, user, job_id)
    d = db.scalar(select(JobDecision).where(JobDecision.job_id == j.id, JobDecision.is_current == True))  # noqa
    cm, am = _client_map(db), _agent_map(db)
    out = job_row(db, j, d, cm, am, agent_view=user.role == "AGENT")
    facts = {"job_ref": out["ref"], "client_name": out["client"], "agent_name": out["agent"], "job_date": out["job_date"],
             "job_type": j.job_type, "invoice_no": out.get("client_invoice")}
    if user.role == "AGENT":
        out["explanations"] = [rc.render(r.code, dict(facts, **(r.evidence or {})), "agent") for r in (d.reasons if d else [])]
        out["status_label"] = {"READY": "Ready for invoicing", "BLOCKED": "On hold", "NEEDS_REVIEW": "Under review"}.get(d.status if d else "", "-")
        if d and d.frozen:
            inv = db.scalar(select(Invoice).join(InvoiceLine).where(InvoiceLine.job_id == j.id, InvoiceLine.kind == "AGENT"))
            out["my_invoice"] = {"id": inv.id, "number": inv.number, "status": inv.status, "issue_date": _d(inv.issue_date)} if inv else None
        out["explanations"] = out["explanations"] or (["This job has been invoiced."] if d and d.frozen else
                                                      ["All checks passed. This job will appear on your next invoice."] if d and d.status == "READY" else [])
        return out
    out.update({"verified_by": j.verified_by, "verification_timestamp": _d(j.verification_timestamp),
                "occupancy_status": j.occupancy_status, "source_system": j.source_system, "source_record_id": j.source_record_id,
                "notes": j.notes, "notes_untrusted": True, "attributes": j.attributes or {}})
    if d:
        out["decision"] = {"id": d.id, "status": d.status, "client_net": _d(d.client_net), "agent_net": _d(d.agent_net),
                           "matched_rules": d.matched_rules, "engine_version": d.engine_version, "decided_at": _d(d.decided_at),
                           "frozen": d.frozen,
                           "reasons": [{"code": r.code, "title": rc.CATALOGUE[r.code].title if r.code in rc.CATALOGUE else r.code,
                                        "message": r.message, "evidence": r.evidence, "rules": r.rules, "team": r.team,
                                        "outcome": rc.CATALOGUE[r.code].outcome if r.code in rc.CATALOGUE else ""} for r in d.reasons]}
        out["trace"] = full_trace(db, j, d)
    hist = list(db.scalars(select(JobDecision).where(JobDecision.job_id == j.id).order_by(JobDecision.id)))
    out["decision_history"] = [{"id": h.id, "status": h.status, "client_net": _d(h.client_net), "decided_at": _d(h.decided_at),
                                "current": h.is_current, "codes": [r.code for r in h.reasons]} for h in hist]
    ex = db.scalar(select(ExceptionCase).where(ExceptionCase.job_id == j.id, ExceptionCase.state == "OPEN"))
    out["open_exception"] = ex.id if ex else None
    out["reviews"] = [{"id": r.id, "reviewer": r.reviewer, "action": r.action, "reason": r.reason, "old": r.old_status,
                       "new": r.new_status, "at": _d(r.created_at), "outcome": r.outcome}
                      for r in db.scalars(select(ReviewDecision).where(ReviewDecision.job_id == j.id).order_by(ReviewDecision.id))]
    out["audit"] = [{"ts": _d(a.ts), "actor": a.actor, "action": a.action, "old": a.old_value, "new": a.new_value, "reason": a.reason}
                    for a in db.scalars(select(AuditEvent).where(AuditEvent.entity == "job", AuditEvent.entity_id == str(j.id)).order_by(AuditEvent.id))]
    return out


def full_trace(db, j: Job, d: JobDecision) -> list[dict]:
    tr = [dict(n) for n in d.trace]
    for n in tr:
        if n["key"] == "verification":
            n["detail"] = dict(n["detail"], timestamp=_d(j.verification_timestamp))
    if d.frozen:
        lines = list(db.execute(select(InvoiceLine, Invoice).join(Invoice).where(InvoiceLine.job_id == j.id)))
        cl = next(((ln, inv) for ln, inv in lines if ln.kind == "CLIENT"), None)
        ag = next(((ln, inv) for ln, inv in lines if ln.kind == "AGENT"), None)
        if cl:
            vat = (Decimal(cl[1].vat_rate) * cl[0].net).quantize(Decimal("0.01"))
            tr.append({"key": "invoice", "label": "Client invoice", "value": cl[1].number, "state": "ok",
                       "detail": {"line_net": str(cl[0].net), "vat_rate": cl[1].vat_rate, "line_vat_indicative": str(vat),
                                  "invoice_gross": str(cl[1].gross), "issued": _d(cl[1].issue_date)}, "ref": f"invoice:{cl[1].id}"})
        if ag:
            tr.append({"key": "agent_invoice", "label": "Agent invoice", "value": ag[1].number, "state": "ok",
                       "detail": {"line_net": str(ag[0].net), "vat_rate": ag[1].vat_rate, "self_billed": ag[1].self_billed,
                                  "treatment": ag[1].vat_treatment}, "ref": f"invoice:{ag[1].id}"})
    return tr


def invoice_row(inv: Invoice, cm, am) -> dict:
    party = cm.get(inv.client_id) if inv.kind == "CLIENT" else am.get(inv.agent_id)
    return {"id": inv.id, "number": inv.number, "kind": inv.kind, "party": party.name if party else None,
            "party_code": party.code if party else None, "issue_date": _d(inv.issue_date), "net": _d(inv.net),
            "vat": _d(inv.vat), "gross": _d(inv.gross), "vat_rate": inv.vat_rate, "status": inv.status,
            "lines": len(inv.lines), "self_billed": inv.self_billed, "exported": bool(inv.exported_at), "batch_id": inv.batch_id}


def search_invoices(db, user, f: dict) -> dict:
    qy = select(Invoice)
    if user.role == "AGENT":
        qy = qy.where(Invoice.kind == "AGENT", Invoice.agent_id == user.agent_id)
    else:
        if f.get("kind"):
            qy = qy.where(Invoice.kind == str(f["kind"]).upper())
        if f.get("client"):
            qy = qy.where(Invoice.client_id == resolve_client(db, f["client"]))
        if f.get("agent"):
            qy = qy.where(Invoice.agent_id == resolve_agent(db, f["agent"]))
        if f.get("batch_id"):
            qy = qy.where(Invoice.batch_id == int(f["batch_id"]))
    if f.get("q"):
        qy = qy.where(Invoice.number.ilike(f"%{str(f['q'])[:30]}%"))
    cm, am = _client_map(db), _agent_map(db)
    rows = [invoice_row(i, cm, am) for i in db.scalars(qy.order_by(desc(Invoice.id)))]
    return {"total": len(rows), "rows": rows}


def get_invoice(db, user, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise NotFound("Invoice not found")
    if user.role == "AGENT" and (inv.kind != "AGENT" or inv.agent_id != user.agent_id):
        raise NotFound("Invoice not found")
    return inv


def invoice_by_number(db, user, number: str) -> Invoice:
    inv = db.scalar(select(Invoice).where(Invoice.number == number))
    if not inv:
        raise NotFound("Invoice not found")
    return get_invoice(db, user, inv.id)


def _company(db) -> dict:
    from ..models import Setting
    st = db.get(Setting, "company")
    c = st.value if st else {}
    return {"name": c.get("name") or "Company name not set", "vat_number": c.get("vat_number") or "-",
            "address": c.get("address") or ""}


def invoice_detail(db, user, invoice_id: int) -> dict:
    inv = get_invoice(db, user, invoice_id)
    cm, am = _client_map(db), _agent_map(db)
    out = invoice_row(inv, cm, am)
    party = cm.get(inv.client_id) if inv.kind == "CLIENT" else am.get(inv.agent_id)
    out.update({"vat_treatment": inv.vat_treatment, "self_billing_ref": inv.self_billing_ref, "po_refs": inv.po_refs,
                "config_snapshot": inv.config_snapshot if user.role != "AGENT" else None,
                "party_detail": ({"name": party.name, "code": party.code, "vat_number": getattr(party, "vat_number", None),
                                  "supplier_type": getattr(party, "supplier_type", None), "vat_status": getattr(party, "vat_status", None),
                                  "billing_email": getattr(party, "billing_email", None)} if party else None),
                "issuer": _company(db),
                "lines": [{"id": ln.id, "job_id": ln.job_id, "job_ref": f"FG-{ln.job_id:05d}", "description": ln.description,
                           "po_number": ln.po_number, "net": _d(ln.net)} for ln in inv.lines]})
    # the matching invoice on the other side of the same jobs
    if user.role != "AGENT":
        other = sorted({n for (n,) in db.execute(select(Invoice.number).join(InvoiceLine).where(
            InvoiceLine.job_id.in_([ln.job_id for ln in inv.lines]), Invoice.kind != inv.kind))})
        out["counterpart_invoices"] = other
    return out

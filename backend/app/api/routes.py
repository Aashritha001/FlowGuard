"""All API endpoints. Every handler is guarded by a permission from security.PERMISSIONS."""
import csv
import io
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, func, desc

from .router import route, HTTPError, Response, body, ERRORS
from ..config import settings
from ..models import (User, Client, Agent, Contract, RateCard, RateCardVersion, VATConfiguration, SelfBillingAgreement,
                      ExceptionCase, ReviewDecision, Invoice, InvoiceBatch, ConfigProposal, ConfigApproval, ImportBatch,
                      ImportRow, MappingTemplate, Integration, ReconciliationRun, AuditEvent, AIInteraction,
                      AutomationOpportunity, Notification, Setting, Job, JobDecision, JobType)
from ..security import (verify_password, create_session, destroy_session, rate_limited, record_attempt, matrix, ROLES,
                        hash_password)
from ..models import Session as DbSession
from .. import workbook
import base64
import re as _re
from ..engine import reason_codes as rc
from ..engine.pricing import SAFETY_CONTROLS, ENGINE_VERSION
from ..services import (queries, analytics, processing, invoicing, reconciliation, reconstruction, config_service,
                        review, imports, export, automation, fields, invoice_docs)
from ..services import snapshot as snapmod
from ..services.audit import log
from ..ai import gateway

ROADMAP = [
    {"phase": 1, "name": "Historical CSV validation", "state": "Working in prototype"},
    {"phase": 2, "name": "Read-only GSC API / database connection", "state": "Integration-ready (adapters defined)"},
    {"phase": 3, "name": "Live Shadow Mode", "state": "Working on CSV; live feed integration-ready"},
    {"phase": 4, "name": "Controlled Finance pilot", "state": "Planned"},
    {"phase": 5, "name": "Accounting / Sage integration", "state": "Sage-ready CSV export with configurable mapping"},
    {"phase": 6, "name": "Approved straight-through processing", "state": "Planned"},
]


def _s(v):
    return None if v is None else str(v)


def _int(v, name="id"):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPError(400, f"{name} must be a number")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except queries.NotFound as e:
        raise HTTPError(404, str(e))
    except (config_service.ProposalError, review.ReviewError, imports.ImportError_, fields.FieldError, ValueError) as e:
        raise HTTPError(400, str(e))
    except workbook.WorkbookError as e:
        raise HTTPError(400, str(e))
    except invoicing.InvoiceSafetyError as e:
        raise HTTPError(409, f"Safety control prevented invoicing: {e}")


# ------------------------------------------------------------------ auth
@route("POST", "/api/auth/login", auth=False)
def login(db, req):
    b = body(req)
    u, pw = str(b.get("username", ""))[:64].strip().lower(), str(b.get("password", ""))[:200]
    key = f"{u}|{req.client_ip}"
    if rate_limited(key):
        log(db, u or "?", "LOGIN_RATE_LIMITED", "auth", "", details={"ip": req.client_ip})
        db.commit()
        raise HTTPError(429, "Too many sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.username == u))
    if not user or not user.active or not verify_password(pw, user.password_hash):
        record_attempt(key)
        log(db, u or "?", "LOGIN_FAILED", "auth", "")
        db.commit()
        raise HTTPError(401, "Username or password is incorrect")
    token, csrf = create_session(db, user)
    log(db, user.username, "LOGIN", "auth", user.id, role=user.role)
    db.commit()
    return Response(body={"user": _me(db, user), "csrf_token": csrf}, set_session=(token, csrf))


@route("POST", "/api/auth/logout", csrf=False)
def logout(db, req):
    log(db, req.user.username, "LOGOUT", "auth", req.user.id, role=req.user.role)
    destroy_session(db, req.token)
    return Response(body={"ok": True}, clear_session=True)


def _me(db, u):
    out = {"username": u.username, "display_name": u.display_name, "role": u.role, "mfa_enrolled": u.mfa_enrolled,
           "agent_id": u.agent_id, "experience": "agent" if u.role == "AGENT" else "staff"}
    if u.agent_id:
        a = db.get(Agent, u.agent_id)
        out["agent"] = {"code": a.code, "name": a.name}
    return out


@route("GET", "/api/auth/me")
def me(db, req):
    return {"user": _me(db, req.user), "csrf_token": req.session.csrf_token}


@route("GET", "/api/meta")
def meta(db, req):
    return {"reason_codes": [{"code": d.code, "outcome": d.outcome, "priority": d.priority, "team": d.team, "title": d.title,
                              "template": d.template} for d in rc.CATALOGUE.values()],
            "safety_controls": SAFETY_CONTROLS, "engine_version": ENGINE_VERSION, "roadmap": ROADMAP,
            "business_date": snapmod.today().isoformat(), "business_date_fixed": bool(settings.business_date),
            "storage_ephemeral": settings.storage_ephemeral,
            "company": (db.get(Setting, "company").value if db.get(Setting, "company") else {}),
            "embedded": settings.embedded,
            "job_types": [{"code": t.code, "name": t.name} for t in db.scalars(select(JobType))],
            "clients": [{"id": c.id, "code": c.code, "name": c.name} for c in db.scalars(select(Client))] if req.user.role != "AGENT" else [],
            "agents": [{"id": a.id, "code": a.code, "name": a.name} for a in db.scalars(select(Agent))] if req.user.role != "AGENT" else [],
            "price_fields": [{"key": f["key"], "label": f["label"], "kind": f["kind"], "choices": f["choices"], "unit": f["unit"],
                              "required_for": f["required_for"]} for f in fields.list_fields(db) if f["status"] == "ACTIVE"]
            if req.user.role != "AGENT" else []}


@route("GET", "/api/rbac", perm="users.manage|audit.read|config.read")
def rbac(db, req):
    return {"roles": ROLES, "matrix": matrix()}


# ------------------------------------------------------------------ dashboard / money map
@route("GET", "/api/dashboard", perm="dashboard.read")
def dashboard(db, req):
    return analytics.dashboard(db)


@route("GET", "/api/money-map", perm="dashboard.read")
def money_map(db, req):
    return analytics.money_map(db)


# ------------------------------------------------------------------ jobs
@route("GET", "/api/jobs", perm="jobs.read|jobs.read_own")
def jobs(db, req):
    return _wrap(queries.search_jobs, db, req.user, req.query)


@route("GET", "/api/jobs/{job_id}", perm="jobs.read|jobs.read_own")
def job(db, req, job_id):
    return _wrap(queries.job_detail, db, req.user, _int(job_id))


@route("POST", "/api/jobs/process", perm="jobs.process")
def process(db, req):
    return processing.process(db, actor=req.user.username)


@route("POST", "/api/jobs/{job_id}/explain", perm="jobs.read|jobs.read_own")
def explain(db, req, job_id):
    _wrap(queries.get_job, db, req.user, _int(job_id))
    return gateway.explain_for_job(db, req.user, _int(job_id))


# ------------------------------------------------------------------ review centre
def _ex_row(db, e, cm, am):
    j = db.get(Job, e.job_id)
    d = db.get(JobDecision, e.decision_id)
    return {"id": e.id, "job_id": j.id, "job_ref": snapmod.job_ref(j), "external_job_id": j.external_job_id,
            "client": cm[j.client_id].name if j.client_id in cm else None, "agent": am[j.agent_id].name if j.agent_id in am else None,
            "job_type": j.job_type, "job_date": _s(j.job_date), "status": e.status_kind, "reason_code": e.reason_code,
            "title": rc.CATALOGUE[e.reason_code].title, "message": next((r.message for r in d.reasons if r.code == e.reason_code), ""),
            "all_codes": (e.context or {}).get("all_codes", []), "priority": e.priority, "value_at_stake": _s(e.value_at_stake),
            "team": e.team, "state": e.state, "created_at": _s(e.created_at), "actions": review.ACTIONS.get(e.reason_code, ["REFER"]),
            "referred_to": (e.context or {}).get("referred_to"), "verification": j.verification_status}


@route("GET", "/api/reviews", perm="reviews.read")
def reviews(db, req):
    q = req.query
    qy = select(ExceptionCase).where(ExceptionCase.state == (q.get("state") or "OPEN").upper())
    if q.get("priority"):
        qy = qy.where(ExceptionCase.priority == q["priority"].upper())
    if q.get("reason"):
        qy = qy.where(ExceptionCase.reason_code == q["reason"].upper())
    if q.get("include_unverified") != "yes":
        qy = qy.where(ExceptionCase.reason_code.notin_(["UNVERIFIED_JOB", "REJECTED_JOB"]))
    cm = {c.id: c for c in db.scalars(select(Client))}
    am = {a.id: a for a in db.scalars(select(Agent))}
    order = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2}
    rows = [_ex_row(db, e, cm, am) for e in db.scalars(qy)]
    rows.sort(key=lambda r: (order[r["priority"]], -float(r["value_at_stake"] or 0)))
    counts = {p: sum(1 for r in rows if r["priority"] == p) for p in order}
    return {"rows": rows[:500], "total": len(rows), "counts": counts,
            "value": str(sum((Decimal(r["value_at_stake"]) for r in rows if r["value_at_stake"]), Decimal("0.00")))}


@route("GET", "/api/reviews/{ex_id}", perm="reviews.read")
def review_get(db, req, ex_id):
    e = db.get(ExceptionCase, _int(ex_id))
    if not e:
        raise HTTPError(404, "Exception not found")
    cm = {c.id: c for c in db.scalars(select(Client))}
    am = {a.id: a for a in db.scalars(select(Agent))}
    out = _ex_row(db, e, cm, am)
    out["job"] = queries.job_detail(db, req.user, e.job_id)
    out["fix_hint"] = review.FIX_HINT.get(e.reason_code)
    out["history"] = [{"reviewer": r.reviewer, "action": r.action, "reason": r.reason, "at": _s(r.created_at)}
                      for r in db.scalars(select(ReviewDecision).where(ReviewDecision.exception_id == e.id))]
    return out


@route("POST", "/api/reviews/{ex_id}/decide", perm="reviews.decide")
def review_decide(db, req, ex_id):
    b = body(req)
    return _wrap(review.decide, db, req.user.username, req.user.role, _int(ex_id), str(b.get("action", "")),
                 str(b.get("reason", "")), b.get("params") or {})


# ------------------------------------------------------------------ invoices
@route("GET", "/api/invoices", perm="invoices.read|invoices.read_own")
def invoices(db, req):
    return queries.search_invoices(db, req.user, req.query)


@route("GET", "/api/invoices/eligible", perm="invoices.generate|invoices.read")
def invoices_eligible(db, req):
    rows = invoicing.eligible(db)
    return {"jobs": len(rows), "client_net": str(sum((d.client_net for _, d in rows), Decimal("0.00"))),
            "agent_net": str(sum((d.agent_net for _, d in rows), Decimal("0.00"))),
            "clients": len({j.client_id for j, _ in rows}), "agents": len({j.agent_id for j, _ in rows})}


@route("POST", "/api/invoices/generate", perm="invoices.generate")
def invoices_generate(db, req):
    b = body(req)
    def _opt_date(k):
        v = b.get(k)
        if not v:
            return None
        try:
            return datetime.strptime(str(v), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPError(400, f"{k} must be a date (YYYY-MM-DD)")
    batch = _wrap(invoicing.generate, db, req.user.username, client_id=b.get("client_id"),
                  issue_date=_opt_date("issue_date"), agent_issue_date=_opt_date("agent_issue_date"),
                  verified_by=_opt_date("verified_by"))
    if not batch:
        raise HTTPError(400, "No READY jobs are awaiting invoicing" + (" for that cut-off" if b.get("verified_by") else ""))
    db.commit()
    recon = reconciliation.run(db, req.user.username)
    return {"batch": batch.reference, "batch_id": batch.id, "expected": batch.expected,
            "invoices": db.scalar(select(func.count()).select_from(Invoice).where(Invoice.batch_id == batch.id)),
            "reconciliation_mismatches": recon.mismatch_count}


@route("GET", "/api/invoices/{inv_id}", perm="invoices.read|invoices.read_own")
def invoice(db, req, inv_id):
    return _wrap(queries.invoice_detail, db, req.user, _int(inv_id))


@route("GET", "/api/invoices/{inv_id}/reconstruct", perm="invoices.read|invoices.read_own")
def invoice_reconstruct(db, req, inv_id):
    _wrap(queries.get_invoice, db, req.user, _int(inv_id))
    res = reconstruction.reproduce(db, _int(inv_id))
    log(db, req.user.username, "INVOICE_RECONSTRUCTED", "invoice", res["invoice"], new=res["result"], role=req.user.role)
    db.commit()
    if req.user.role == "AGENT":
        for ln in res["lines"]:
            ln.pop("trace", None); ln.pop("rules", None); ln.pop("rate_card", None); ln.pop("contract", None)
        res.pop("config_snapshot", None)
    return res


@route("GET", "/api/invoice-list.csv", perm="invoices.read|invoices.read_own")
def invoices_csv(db, req):
    rows = queries.search_invoices(db, req.user, req.query)["rows"]
    log(db, req.user.username, "INVOICE_LIST_DOWNLOADED", "invoice", "", new=f"{len(rows)} invoices", role=req.user.role)
    db.commit()
    return Response(body=invoice_docs.list_csv(rows), content_type="text/csv", filename="flowguard_invoices.csv")


@route("GET", "/api/invoices/{inv_id}/pdf", perm="invoices.read|invoices.read_own")
def invoice_pdf(db, req, inv_id):
    inv = _wrap(queries.get_invoice, db, req.user, _int(inv_id))  # agents: own invoices only (404 otherwise)
    data = invoice_docs.invoice_pdf(db, req.user.username, req.user.role, inv)
    return Response(body=data, content_type="application/pdf", filename=invoice_docs.filename(inv))


@route("GET", "/api/batches/{batch_id}/pdfs", perm="invoices.read")
def batch_pdfs(db, req, batch_id):
    b = db.get(InvoiceBatch, _int(batch_id))
    if not b:
        raise HTTPError(404, "Batch not found")
    return Response(body=invoice_docs.batch_zip(db, req.user.username, req.user.role, b), content_type="application/zip",
                    filename=f"{b.reference}_invoices.zip")


@route("GET", "/api/batches", perm="invoices.read")
def batches(db, req):
    return {"rows": [{"id": b.id, "reference": b.reference, "created_by": b.created_by, "created_at": _s(b.created_at),
                      "expected": b.expected} for b in db.scalars(select(InvoiceBatch).order_by(desc(InvoiceBatch.id)))]}


# ------------------------------------------------------------------ reconciliation / export
@route("GET", "/api/reconciliation", perm="reconciliation.read")
def recon(db, req):
    runs = [{"id": r.id, "at": _s(r.created_at), "by": r.run_by, "mismatches": r.mismatch_count}
            for r in db.scalars(select(ReconciliationRun).order_by(desc(ReconciliationRun.id)).limit(20))]
    return {"current": reconciliation.compute(db), "runs": runs}


@route("POST", "/api/reconciliation/run", perm="reconciliation.run")
def recon_run(db, req):
    r = reconciliation.run(db, req.user.username)
    return {"id": r.id, "result": r.result}


@route("GET", "/api/exports/sage", perm="export.sage")
def sage(db, req):
    b = req.query.get("batch_id")
    csv_text = export.build_csv(db, req.user.username, int(b) if b else None, mark_exported=req.user.role == "FINANCE_ADMIN")
    return Response(body=csv_text, content_type="text/csv", filename="flowguard_sage_export.csv")


@route("GET", "/api/exports/mapping", perm="export.sage|config.read")
def sage_mapping(db, req):
    return {"columns": export.mapping(db), "label": "Sage-ready export / configurable accounting mapping",
            "nominals": export.NOMINALS}


# ------------------------------------------------------------------ parties and configuration
@route("GET", "/api/clients", perm="parties.read")
def clients(db, req):
    out = []
    for c in db.scalars(select(Client)):
        n = db.scalar(select(func.count()).select_from(Job).where(Job.client_id == c.id, Job.shadow == False))  # noqa
        out.append({"id": c.id, "code": c.code, "name": c.name, "sector": c.sector, "po_required": c.po_required,
                    "billing_email": c.billing_email, "sage_account_ref": c.sage_account_ref, "jobs": n})
    return {"rows": out}


@route("GET", "/api/agents", perm="parties.read")
def agents(db, req):
    out = []
    for a in db.scalars(select(Agent)):
        sba = [s for s in db.scalars(select(SelfBillingAgreement).where(SelfBillingAgreement.agent_id == a.id))]
        n = db.scalar(select(func.count()).select_from(Job).where(Job.agent_id == a.id, Job.shadow == False))  # noqa
        out.append({"id": a.id, "code": a.code, "name": a.name, "supplier_type": a.supplier_type, "vat_status": a.vat_status,
                    "vat_number": a.vat_number, "self_billing": a.self_billing, "region": a.region, "jobs": n,
                    "agreements": [{"ref": s.reference, "from": _s(s.valid_from), "to": _s(s.valid_to), "status": s.status} for s in sba]})
    return {"rows": out}


@route("GET", "/api/contracts", perm="config.read")
def contracts(db, req):
    cm = {c.id: c for c in db.scalars(select(Client))}
    return {"rows": [{"id": c.id, "client": cm[c.client_id].name, "code": c.code, "version": c.version, "valid_from": _s(c.valid_from),
                      "valid_to": _s(c.valid_to), "status": c.status, "terms": c.terms} for c in db.scalars(select(Contract))]}


def _rcv(v):
    return {"id": v.id, "version": v.version, "valid_from": _s(v.valid_from), "valid_to": _s(v.valid_to), "status": v.status,
            "precedence": v.precedence, "note": v.note, "created_by": v.created_by, "approved_by": v.approved_by,
            "proposal_id": v.proposal_id, "rollback_of_version": v.rollback_of_version, "created_at": _s(v.created_at),
            "rules": [{"id": r.id, "code": r.rule_code, "kind": r.kind, "job_type": r.job_type, "condition": r.condition,
                       "group": r.group, "client_amount": _s(r.client_amount), "agent_amount": _s(r.agent_amount),
                       "clause_ref": r.clause_ref} for r in v.rules]}


@route("GET", "/api/rate-cards", perm="config.read")
def rate_cards(db, req):
    cm = {c.id: c for c in db.scalars(select(Client))}
    return {"rows": [{"id": c.id, "name": c.name, "client": cm[c.client_id].name, "client_code": cm[c.client_id].code,
                      "contract_code": c.contract_code, "versions": [_rcv(v) for v in c.versions]} for c in db.scalars(select(RateCard))]}


@route("GET", "/api/rules", perm="config.read")
def rules(db, req):
    return {"safety_controls": SAFETY_CONTROLS,
            "reason_codes": [{"code": d.code, "outcome": d.outcome, "priority": d.priority, "team": d.team, "title": d.title,
                              "template": d.template, "agent_template": d.agent_template} for d in rc.CATALOGUE.values()],
            "config_health": config_service.health(db),
            "vat": [{"id": v.id, "version": v.version, "rate": v.standard_rate, "from": _s(v.valid_from), "to": _s(v.valid_to),
                     "status": v.status, "note": v.note} for v in db.scalars(select(VATConfiguration))],
            "four_eyes_policy": config_service.four_eyes_policy(db),
            "job_types": [{"code": t.code, "name": t.name} for t in db.scalars(select(JobType))],
            "review_routing": {c: {"team": d.team, "priority": d.priority} for c, d in rc.CATALOGUE.items()}}


@route("PUT", "/api/settings/business-date", perm="settings.business_date")
def set_business_date(db, req):
    """Fixed business date (e.g. to close a past period), or empty for today's real date. Re-runs Lane A."""
    if settings.business_date_env:
        raise HTTPError(409, "The business date is fixed by the FLOWGUARD_BUSINESS_DATE environment variable")
    v = str(body(req).get("date") or "").strip()
    if v:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise HTTPError(400, "date must be YYYY-MM-DD, or empty for today's date")
    old = settings.business_date or "real date"
    st = db.get(Setting, "business_date")
    if st:
        st.value = {"date": v}
    else:
        db.add(Setting(key="business_date", value={"date": v}))
    settings.business_date = v
    log(db, req.user.username, "BUSINESS_DATE_CHANGED", "setting", "business_date", old=old, new=v or "real date",
        role=req.user.role)
    db.commit()
    res = processing.process(db, actor=req.user.username)
    return {"business_date": snapmod.today().isoformat(), "fixed": bool(v), "counts": res["counts"], "changed": res["changed"]}


@route("PUT", "/api/settings/four-eyes", perm="users.manage")
def set_four_eyes(db, req):
    mode = body(req).get("mode")
    if mode not in ("ALL", "HIGH_RISK_ONLY"):
        raise HTTPError(400, "mode must be ALL or HIGH_RISK_ONLY")
    s = db.get(Setting, "four_eyes_policy")
    old = s.value["mode"] if s else "ALL"
    if s:
        s.value = {"mode": mode}
    else:
        db.add(Setting(key="four_eyes_policy", value={"mode": mode}))
    log(db, req.user.username, "FOUR_EYES_POLICY_CHANGED", "setting", "four_eyes_policy", old=old, new=mode, role=req.user.role)
    db.commit()
    return {"mode": mode}


def _prop(db, p, full=False):
    out = {"id": p.id, "kind": p.kind, "title": p.title, "source": p.source, "status": p.status, "risk_level": p.risk_level,
           "risk_reasons": p.risk_reasons, "conflicts": p.conflicts, "created_by": p.created_by, "created_at": _s(p.created_at),
           "approvals_required": p.approvals_required, "resulting_rcv_id": p.resulting_rcv_id,
           "approvals": [{"approver": a.approver, "decision": a.decision, "reason": a.reason, "at": _s(a.created_at)}
                         for a in db.scalars(select(ConfigApproval).where(ConfigApproval.proposal_id == p.id))]}
    if full:
        out.update({"payload": p.payload, "simulation": p.simulation})
    else:
        sim = p.simulation or {}
        out["impact"] = {k: sim.get(k) for k in ("affected_jobs", "billing_difference", "historical_invoices_affected")}
    return out


# ------------------------------------------------------------------ price factors (admin-defined fields)
@route("GET", "/api/fields", perm="config.read")
def price_fields(db, req):
    return {"rows": fields.list_fields(db)}


@route("POST", "/api/fields", perm="fields.manage")
def price_field_create(db, req):
    return _wrap(fields.create, db, req.user.username, body(req))


@route("PUT", "/api/fields/{key}", perm="fields.manage")
def price_field_update(db, req, key):
    return _wrap(fields.update, db, req.user.username, str(key), body(req))


@route("POST", "/api/fields/{key}/retire", perm="fields.manage")
def price_field_retire(db, req, key):
    return _wrap(fields.retire, db, req.user.username, str(key), str(body(req).get("reason", "")))


@route("PUT", "/api/jobs/{job_id}/fields", perm="jobs.edit_fields")
def job_fields(db, req, job_id):
    b = body(req)
    return _wrap(fields.set_job_values, db, req.user.username, req.user.role, _int(job_id), b.get("values") or {},
                 str(b.get("reason", "")))


@route("GET", "/api/config/proposals", perm="config.read")
def proposals(db, req):
    return {"rows": [_prop(db, p) for p in db.scalars(select(ConfigProposal).order_by(desc(ConfigProposal.id)))],
            "four_eyes_policy": config_service.four_eyes_policy(db)}


@route("GET", "/api/config/proposals/{pid}", perm="config.read")
def proposal(db, req, pid):
    p = db.get(ConfigProposal, _int(pid))
    if not p:
        raise HTTPError(404, "Proposal not found")
    return _prop(db, p, full=True)


@route("POST", "/api/config/simulate", perm="config.propose|config.read")
def simulate(db, req):
    b = body(req)
    return _wrap(config_service.simulate, db, str(b.get("kind", "")), b.get("payload") or {})


@route("POST", "/api/config/proposals", perm="config.propose")
def proposal_create(db, req):
    b = body(req)
    p = _wrap(config_service.create_proposal, db, req.user.username, str(b.get("kind", "")), b.get("payload") or {},
              str(b.get("title", ""))[:200])
    return _prop(db, p, full=True)


@route("POST", "/api/config/proposals/{pid}/decide", perm="config.approve")
def proposal_decide(db, req, pid):
    b = body(req)
    p = _wrap(config_service.decide, db, req.user.username, req.user.role, _int(pid), str(b.get("decision", "")).upper(),
              str(b.get("reason", "")))
    return _prop(db, p, full=True)


# ------------------------------------------------------------------ imports / integrations
def _batch(b, rows=None):
    out = {"id": b.id, "filename": b.filename, "uploaded_by": b.uploaded_by, "status": b.status, "headers": b.headers,
           "mapping": b.mapping, "summary": b.summary, "shadow_result": b.shadow_result, "created_at": _s(b.created_at)}
    if rows is not None:
        out["preview"] = rows
    return out


@route("GET", "/api/imports", perm="imports.read|imports.run")
def import_list(db, req):
    return {"rows": [_batch(b) for b in db.scalars(select(ImportBatch).order_by(desc(ImportBatch.id)))],
            "fields": dict({k: {"required": v[0], "type": v[1]} for k, v in imports.FIELDS.items()},
                           **{f"field:{f['key']}": {"required": False, "type": f["kind"].lower(), "label": f["label"]}
                              for f in fields.list_fields(db) if f["status"] == "ACTIVE"}),
            "templates": [{"id": t.id, "name": t.name, "mapping": t.mapping} for t in db.scalars(select(MappingTemplate))],
            "limits": {"max_bytes": settings.max_upload_bytes, "extensions": imports.ALLOWED_EXT, "max_rows": imports.MAX_ROWS}}


@route("GET", "/api/imports/{bid}", perm="imports.read|imports.run")
def import_get(db, req, bid):
    b = db.get(ImportBatch, _int(bid))
    if not b:
        raise HTTPError(404, "Import not found")
    rows = [{"row": r.row_no, "status": r.status, "errors": r.errors, "raw": r.raw,
             "shadow": (r.mapped or {}).get("_shadow")} for r in
            db.scalars(select(ImportRow).where(ImportRow.batch_id == b.id).order_by(ImportRow.row_no).limit(400))]
    return _batch(b, rows)


@route("POST", "/api/imports", perm="imports.run")
def import_upload(db, req):
    b = body(req)
    return _batch(_wrap(imports.upload, db, req.user.username, str(b.get("filename", "")), str(b.get("content", ""))))


@route("POST", "/api/imports/workbook", perm="imports.run")
def import_workbook(db, req):
    """GSC data pack (.xlsx workbook or its single-file CSV form). Arrives base64-encoded in JSON; size-checked and
    parsed read-only."""
    b = body(req)
    name = imports.safe_filename(str(b.get("filename", "workbook.xlsx")))
    if not name.lower().endswith((".xlsx", ".csv")):
        raise HTTPError(400, "Upload the data pack as an .xlsx workbook or its .csv form")
    try:
        raw = base64.b64decode(str(b.get("content_b64", "")), validate=True)
    except Exception:
        raise HTTPError(400, "File content is not valid base64")
    if not raw or len(raw) > settings.max_upload_bytes:
        raise HTTPError(413, f"Workbook must be under {settings.max_upload_bytes // (1024 * 1024)} MB")
    pack = _wrap(workbook.read_pack, raw)
    return _wrap(workbook.import_workbook, db, req.user.username, pack, name)


@route("POST", "/api/imports/{bid}/map", perm="imports.run")
def import_map(db, req, bid):
    b = body(req)
    return _batch(_wrap(imports.apply_mapping, db, req.user.username, _int(bid), b.get("mapping") or {}, b.get("save_as")))


@route("POST", "/api/imports/{bid}/shadow", perm="imports.run")
def import_shadow(db, req, bid):
    return _wrap(imports.run_shadow, db, req.user.username, _int(bid))


@route("POST", "/api/imports/{bid}/live", perm="imports.run")
def import_live(db, req, bid):
    return _wrap(imports.import_live, db, req.user.username, _int(bid))


@route("GET", "/api/imports/{bid}/errors.csv", perm="imports.read|imports.run")
def import_errors(db, req, bid):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row", "errors"])
    for r in db.scalars(select(ImportRow).where(ImportRow.batch_id == _int(bid), ImportRow.status == "INVALID")):
        w.writerow([r.row_no, export.escape_cell("; ".join(r.errors))])
    return Response(body=buf.getvalue(), content_type="text/csv", filename=f"import_{bid}_errors.csv")


@route("GET", "/api/integrations", perm="integrations.read")
def integrations(db, req):
    return {"rows": [{"id": i.id, "name": i.name, "kind": i.kind, "status": i.status, "access_mode": i.access_mode,
                      "description": i.description, "last_sync_at": _s(i.last_sync_at)} for i in db.scalars(select(Integration))],
            "principles": ["Backend-only connectors; credentials never reach the browser or the AI",
                           "Pilot access is READ ONLY with a least-privilege service account",
                           "Only fields needed for pricing, readiness, invoicing, review and audit are retrieved",
                           "Source field names are mapped to the normalised internal schema, never used by business logic"]}


# ------------------------------------------------------------------ audit / AI / automation / notifications
@route("GET", "/api/audit", perm="audit.read")
def audit(db, req):
    q = req.query
    qy = select(AuditEvent)
    if q.get("action"):
        qy = qy.where(AuditEvent.action == q["action"])
    if q.get("actor"):
        qy = qy.where(AuditEvent.actor == q["actor"])
    if q.get("entity"):
        qy = qy.where(AuditEvent.entity == q["entity"])
    if q.get("q"):
        qy = qy.where(AuditEvent.reason.ilike(f"%{q['q'][:40]}%") | AuditEvent.entity_id.ilike(f"%{q['q'][:40]}%"))
    total = db.scalar(select(func.count()).select_from(qy.subquery()))
    page = max(1, int(q.get("page") or 1))
    rows = db.scalars(qy.order_by(desc(AuditEvent.id)).offset((page - 1) * 50).limit(50))
    actions = sorted({a for (a,) in db.execute(select(AuditEvent.action).distinct())})
    return {"total": total, "page": page, "actions": actions, "append_only": True,
            "rows": [{"id": a.id, "ts": _s(a.ts), "actor": a.actor, "role": a.role, "action": a.action, "entity": a.entity,
                      "entity_id": a.entity_id, "old": a.old_value, "new": a.new_value, "reason": a.reason,
                      "config_version": a.config_version, "ai": a.ai_involvement, "details": a.details} for a in rows]}


@route("GET", "/api/ai/status", perm="ai.use")
def ai_status(db, req):
    return gateway.status()


@route("POST", "/api/ai/chat", perm="ai.use")
def ai_chat(db, req):
    b = body(req)
    page = b.get("page") if isinstance(b.get("page"), dict) else {}
    page = {k: v for k, v in page.items() if k in ("route", "job_id", "last_proposal_id") and isinstance(v, (str, int))}
    return gateway.handle(db, req.user, str(b.get("message", "")), page)


@route("GET", "/api/ai/log", perm="ai.read_log")
def ai_log(db, req):
    return {"rows": [{"id": f"AI-{a.id:04d}", "ts": _s(a.ts), "actor": a.actor, "role": a.role, "request_type": a.request_type,
                      "prompt_preview": a.prompt_preview, "tools": a.tools_called, "records": a.records_accessed, "model": a.model,
                      "outcome": a.outcome, "proposal_id": a.proposal_id, "external_transmission": a.external_transmission,
                      "financial_changes": a.financial_changes}
                     for a in db.scalars(select(AIInteraction).order_by(desc(AIInteraction.id)).limit(200))]}


def _opp(o):
    return {"id": o.id, "pattern": o.pattern, "reason_code": o.reason_code, "resolution": o.resolution, "sample_size": o.sample_size,
            "agreement": o.agreement, "open_matching": o.open_matching, "est_monthly_avoided": o.est_monthly_avoided,
            "stp_before": o.stp_before, "stp_after": o.stp_after, "proposed_rule": o.proposed_rule, "status": o.status,
            "proposal_id": o.proposal_id}


@route("GET", "/api/automation", perm="automation.read")
def automation_list(db, req):
    return {"rows": [_opp(o) for o in db.scalars(select(AutomationOpportunity))], "maturity": automation.MATURITY,
            "thresholds": {"min_sample": automation.MIN_SAMPLE, "min_agreement": automation.MIN_AGREEMENT}}


@route("POST", "/api/automation/analyse", perm="automation.read")
def automation_analyse(db, req):
    ops = automation.analyse(db)
    log(db, req.user.username, "AUTOMATION_ANALYSIS", "automation", "", new=f"{len(ops)} opportunities", ai="Pattern analysis only")
    db.commit()
    return {"rows": [_opp(o) for o in ops]}


@route("GET", "/api/automation/{oid}/cases", perm="automation.read")
def automation_cases(db, req, oid):
    o = db.get(AutomationOpportunity, _int(oid))
    rows = [db.get(ReviewDecision, i) for i in o.review_ids]
    return {"rows": [{"id": r.id, "job_id": r.job_id, "job_ref": f"FG-{r.job_id:05d}", "reviewer": r.reviewer,
                      "resolution": r.outcome.get("resolution"), "reason": r.reason, "evidence": r.evidence_ref,
                      "at": _s(r.created_at)} for r in rows if r]}


@route("POST", "/api/automation/{oid}/reject", perm="automation.act")
def automation_reject(db, req, oid):
    return _opp(automation.reject(db, req.user.username, _int(oid), str(body(req).get("reason", "Rejected"))))


@route("POST", "/api/automation/{oid}/draft", perm="automation.act")
def automation_draft(db, req, oid):
    p = _wrap(automation.create_draft_rule, db, req.user.username, _int(oid))
    return _prop(db, p, full=True)


@route("GET", "/api/notifications", perm="notifications.read")
def notifications(db, req):
    aud = f"AGENT:{req.user.agent_id}" if req.user.role == "AGENT" else "STAFF"
    rows = db.scalars(select(Notification).where(Notification.audience == aud).order_by(desc(Notification.id)).limit(100))
    return {"rows": [{"id": n.id, "ts": _s(n.ts), "kind": n.kind, "title": n.title, "body": n.body, "read": n.read} for n in rows]}


@route("POST", "/api/notifications/read", perm="notifications.read")
def notifications_read(db, req):
    aud = f"AGENT:{req.user.agent_id}" if req.user.role == "AGENT" else "STAFF"
    for n in db.scalars(select(Notification).where(Notification.audience == aud, Notification.read == False)):  # noqa
        n.read = True
    db.commit()
    return {"ok": True}


@route("GET", "/api/users", perm="users.manage")
def users(db, req):
    am = {a.id: a for a in db.scalars(select(Agent))}
    return {"rows": [{"username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active,
                      "mfa_enrolled": u.mfa_enrolled, "agent_id": u.agent_id,
                      "agent": f"{am[u.agent_id].code} {am[u.agent_id].name}" if u.agent_id in am else None,
                      "created_at": _s(u.created_at)} for u in db.scalars(select(User).order_by(User.username))]}


MIN_PASSWORD = 12


def _check_password(pw: str):
    if len(pw) < MIN_PASSWORD or len(pw) > 200:
        raise HTTPError(400, f"Password must be at least {MIN_PASSWORD} characters")
    if pw.lower() == pw or pw.upper() == pw or not _re.search(r"\d", pw):
        raise HTTPError(400, "Password needs upper and lower case letters and a number")


def _end_sessions(db, user_id):
    for s_ in db.scalars(select(DbSession).where(DbSession.user_id == user_id)):
        db.delete(s_)


@route("POST", "/api/users", perm="users.manage")
def user_create(db, req):
    b = body(req)
    un = str(b.get("username", "")).strip().lower()
    if not _re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", un):
        raise HTTPError(400, "Username: 3-64 characters, lowercase letters, digits, dot, dash or underscore")
    if db.scalar(select(User).where(User.username == un)):
        raise HTTPError(400, "That username is taken")
    role = str(b.get("role", "")).upper()
    if role not in ROLES:
        raise HTTPError(400, f"Role must be one of {', '.join(ROLES)}")
    name = str(b.get("display_name", "")).strip()[:120] or un
    agent = None
    if role == "AGENT":
        agent = db.scalar(select(Agent).where(Agent.code == str(b.get("agent_code", "")).strip()))
        if not agent:
            raise HTTPError(400, "An agent login must be linked to an existing agent code")
    pw = str(b.get("password", ""))
    _check_password(pw)
    u = User(username=un, display_name=name, role=role, password_hash=hash_password(pw), agent_id=agent.id if agent else None)
    db.add(u)
    log(db, req.user.username, "USER_CREATED", "user", un, new=role, role=req.user.role)
    db.commit()
    return {"username": un, "role": role}


@route("POST", "/api/users/{username}/password", perm="users.manage")
def user_reset_password(db, req, username):
    u = db.scalar(select(User).where(User.username == str(username)))
    if not u:
        raise HTTPError(404, "User not found")
    pw = str(body(req).get("password", ""))
    _check_password(pw)
    u.password_hash = hash_password(pw)
    _end_sessions(db, u.id)
    log(db, req.user.username, "USER_PASSWORD_RESET", "user", u.username, role=req.user.role)
    db.commit()
    return {"ok": True}


@route("POST", "/api/users/{username}/active", perm="users.manage")
def user_active(db, req, username):
    u = db.scalar(select(User).where(User.username == str(username)))
    if not u:
        raise HTTPError(404, "User not found")
    active = bool(body(req).get("active"))
    if u.id == req.user.id and not active:
        raise HTTPError(400, "You cannot deactivate your own account")
    if not active and u.role == "SUPER_ADMIN" and db.scalar(select(func.count()).select_from(User).where(
            User.role == "SUPER_ADMIN", User.active == True)) <= 1:  # noqa: E712
        raise HTTPError(400, "At least one active Super Admin is required")
    u.active = active
    if not active:
        _end_sessions(db, u.id)
    log(db, req.user.username, "USER_ACTIVATED" if active else "USER_DEACTIVATED", "user", u.username, role=req.user.role)
    db.commit()
    return {"ok": True}


@route("POST", "/api/auth/password")
def change_own_password(db, req):
    b = body(req)
    if not verify_password(str(b.get("current", "")), req.user.password_hash):
        raise HTTPError(400, "Current password is incorrect")
    pw = str(b.get("new", ""))
    _check_password(pw)
    req.user.password_hash = hash_password(pw)
    log(db, req.user.username, "PASSWORD_CHANGED", "user", req.user.username, role=req.user.role)
    db.commit()
    return {"ok": True}


@route("GET", "/api/observability", perm="audit.read")
def observability(db, req):
    def count(action):
        return db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == action))
    ai_fail = db.scalar(select(func.count()).select_from(AIInteraction).where(AIInteraction.outcome.in_(["ERROR"])))
    return {"processing_errors": ERRORS["count"], "recent_errors": ERRORS["last"],
            "failed_imports": db.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.status == "INVALID")),
            "ai_failures": ai_fail, "ai_rejections": count("AI_ACTION_REJECTED"), "access_denied": count("ACCESS_DENIED"),
            "rule_conflicts": count("RULE_CONFLICT_DETECTED"), "failed_logins": count("LOGIN_FAILED"),
            "reconciliation_mismatches": reconciliation.compute(db)["mismatch_count"], "integration_failures": 0}


# ------------------------------------------------------------------ agent portal
@route("GET", "/api/agent/dashboard", perm="jobs.read_own")
def agent_dashboard(db, req):
    rows = queries.search_jobs(db, req.user, {"page_size": 200})["rows"]
    invs = queries.search_invoices(db, req.user, {})["rows"]
    c = lambda s: sum(1 for r in rows if r["status"] == s and not r["invoiced"])  # noqa
    return {"total": len(rows), "ready": c("READY"), "review": c("NEEDS_REVIEW"), "blocked": c("BLOCKED"),
            "invoiced": sum(1 for r in rows if r["invoiced"]), "invoices": invs[:5], "recent": rows[:8],
            "awaiting_value": str(sum((Decimal(r["agent_net"]) for r in rows if r["status"] == "READY" and not r["invoiced"] and r["agent_net"]), Decimal("0.00")))}


@route("GET", "/api/agent/profile", perm="jobs.read_own")
def agent_profile(db, req):
    a = db.get(Agent, req.user.agent_id)
    sba = db.scalar(select(SelfBillingAgreement).where(SelfBillingAgreement.agent_id == a.id))
    return {"code": a.code, "name": a.name, "supplier_type": a.supplier_type, "vat_status": a.vat_status,
            "vat_number": a.vat_number, "self_billing": a.self_billing, "region": a.region,
            "agreement": {"ref": sba.reference, "from": _s(sba.valid_from), "to": _s(sba.valid_to)} if sba else None}

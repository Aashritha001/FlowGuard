"""Dashboard KPIs, Money Map and leakage analysis. Every figure is computed live from the database."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from ..models import Job, JobDecision, ExceptionCase, Invoice, InvoiceLine, JobOverride, ReviewDecision, Client, \
    ReconciliationRun
from ..engine import reason_codes as rc
from ..engine.vat import q
from . import snapshot as snapmod
from . import reconciliation

Z = Decimal("0.00")


def _rows(db):
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.shadow == False))}  # noqa
    decs = {d.job_id: d for d in db.scalars(select(JobDecision).where(JobDecision.is_current == True))}  # noqa
    touched = {o.job_id for o in db.scalars(select(JobOverride))} | {r.job_id for r in db.scalars(select(ReviewDecision))}
    exs = list(db.scalars(select(ExceptionCase).where(ExceptionCase.state == "OPEN")))
    return jobs, decs, touched, exs


def bucket_of(code: str) -> str:
    return rc.CATALOGUE[code].leak_bucket if code in rc.CATALOGUE else "other"


BUCKET_LABELS = {"verified_not_invoiced": "Verified but not invoiced", "missing_rate": "Missing rate",
                 "missing_po": "Missing PO", "duplicates": "Possible duplicates", "vat": "VAT exceptions",
                 "overlapping_rules": "Overlapping rules", "other": "Other exceptions"}


def dashboard(db) -> dict:
    jobs, decs, touched, exs = _rows(db)
    verified = [j for j in jobs.values() if j.verification_status == "VERIFIED"]
    status_count = defaultdict(int)
    status_value = defaultdict(lambda: Z)
    unpriced = defaultdict(int)
    for j in verified:
        d = decs.get(j.id)
        if not d:
            continue
        s = "INVOICED" if d.frozen else d.status
        status_count[s] += 1
        if d.value_at_stake is not None:
            status_value[s] += d.value_at_stake
        elif s != "READY":
            unpriced[s] += 1
    auto = sum(1 for j in verified if decs.get(j.id) and decs[j.id].status == "READY" and j.id not in touched)
    stp = (100 * auto / len(verified)) if verified else 0
    # leakage
    leak = {k: {"label": v, "jobs": 0, "value": Z, "unpriced": 0} for k, v in BUCKET_LABELS.items()}
    ready_open = [d for d in decs.values() if d.status == "READY" and not d.frozen and jobs.get(d.job_id)]
    leak["verified_not_invoiced"]["jobs"] = len(ready_open)
    leak["verified_not_invoiced"]["value"] = sum((d.client_net for d in ready_open), Z)
    reasons = defaultdict(lambda: {"jobs": 0, "value": Z})
    for e in exs:
        if e.job_id not in jobs or jobs[e.job_id].verification_status != "VERIFIED":
            continue
        b = bucket_of(e.reason_code)
        leak[b]["jobs"] += 1
        if e.value_at_stake is not None:
            leak[b]["value"] += e.value_at_stake
        else:
            leak[b]["unpriced"] += 1
        reasons[e.reason_code]["jobs"] += 1
        reasons[e.reason_code]["value"] += e.value_at_stake or Z
    held_before = {"UNVERIFIED": 0, "REJECTED": 0}
    for j in jobs.values():
        if j.verification_status in held_before:
            held_before[j.verification_status] += 1
    # trend: weekly straight-through rate by job week
    weeks = defaultdict(lambda: [0, 0])
    for j in verified:
        if not j.job_date or not decs.get(j.id) or j.job_date > snapmod.today():
            continue
        wk = j.job_date - timedelta(days=j.job_date.weekday())
        weeks[wk][1] += 1
        if decs[j.id].status == "READY" and j.id not in touched:
            weeks[wk][0] += 1
    trend = [{"week": w.isoformat(), "stp": round(100 * a / t, 1), "jobs": t} for w, (a, t) in sorted(weeks.items())]
    # top clients by verified value
    clients = {c.id: c for c in db.scalars(select(Client))}
    by_client = defaultdict(lambda: Z)
    for j in verified:
        d = decs.get(j.id)
        if d and d.client_net is not None:
            by_client[j.client_id] += d.client_net
    top_clients = sorted(({"client_id": k, "name": clients[k].name if k in clients else "?", "value": str(q(v))}
                          for k, v in by_client.items()), key=lambda x: -Decimal(x["value"]))[:6]
    invs = list(db.scalars(select(Invoice)))
    recon = reconciliation.compute(db)
    # review time
    rds = list(db.scalars(select(ReviewDecision)))
    exmap = {e.id: e for e in db.scalars(select(ExceptionCase))}
    durations = [(r.created_at - exmap[r.exception_id].created_at).total_seconds() / 3600 for r in rds
                 if r.exception_id in exmap and r.created_at >= exmap[r.exception_id].created_at]
    total_value = sum(status_value.values(), Z)
    return {
        "kpis": {
            "verified_jobs": len(verified),
            "invoice_ready_value": str(q(status_value["READY"])), "invoice_ready_jobs": status_count["READY"],
            "invoiced_value": str(q(status_value["INVOICED"])), "invoiced_jobs": status_count["INVOICED"],
            "blocked_value": str(q(status_value["BLOCKED"])), "blocked_jobs": status_count["BLOCKED"],
            "blocked_unpriced": unpriced["BLOCKED"],
            "review_value": str(q(status_value["NEEDS_REVIEW"])), "review_jobs": status_count["NEEDS_REVIEW"],
            "review_unpriced": unpriced["NEEDS_REVIEW"],
            "straight_through_rate": round(stp, 1), "straight_through_jobs": auto,
            "wrong_output_count": recon["wrong_output_count"], "wrong_output_target": 0,
            "money_at_risk": str(q(sum((v["value"] for k, v in leak.items()), Z))),
            "avg_review_hours": round(sum(durations) / len(durations), 1) if durations else None,
            "exception_rate": round(100 * (status_count["BLOCKED"] + status_count["NEEDS_REVIEW"]) / len(verified), 1) if verified else 0,
            "invoices_generated": len(invs), "reconciliation_mismatches": recon["mismatch_count"],
            "total_verified_value": str(q(total_value)),
        },
        "status": [{"status": s, "jobs": status_count[s], "value": str(q(status_value[s])), "unpriced": unpriced[s]}
                   for s in ("INVOICED", "READY", "NEEDS_REVIEW", "BLOCKED")],
        "held_before_verification": held_before,
        "leakage": [{"bucket": k, **{kk: (str(q(vv)) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()}} for k, v in leak.items()],
        "reasons": sorted(({"code": k, "title": rc.CATALOGUE[k].title, "jobs": v["jobs"], "value": str(q(v["value"])),
                            "bucket": bucket_of(k)} for k, v in reasons.items()), key=lambda x: -x["jobs"]),
        "trend": trend, "top_clients": top_clients,
        "invoices": {"client": sum(1 for i in invs if i.kind == "CLIENT"), "agent": sum(1 for i in invs if i.kind == "AGENT")},
    }


def money_map(db) -> dict:
    jobs, decs, touched, exs = _rows(db)
    allj = list(jobs.values())
    verified = [j for j in allj if j.verification_status == "VERIFIED"]

    def agg(js, pick):
        v = Z
        un = 0
        for j in js:
            d = decs.get(j.id)
            amt = pick(d) if d else None
            if amt is None:
                un += 1
            else:
                v += amt
        return {"jobs": len(js), "value": str(q(v)), "unpriced": un}

    st = lambda s: [j for j in verified if decs.get(j.id) and decs[j.id].status == s and not decs[j.id].frozen]  # noqa
    invoiced = [j for j in verified if decs.get(j.id) and decs[j.id].frozen]
    ready, review, blocked = st("READY"), st("NEEDS_REVIEW"), st("BLOCKED")
    invs = list(db.scalars(select(Invoice)))
    lines = list(db.scalars(select(InvoiceLine)))
    recon = reconciliation.compute(db)
    exported = [i for i in invs if i.exported_at]
    unverified = [j for j in allj if j.verification_status == "UNVERIFIED"]
    rejected = [j for j in allj if j.verification_status == "REJECTED"]
    stake = lambda d: d.value_at_stake  # noqa
    return {"stages": [
        {"id": "received", "label": "Client sends job", "scope": "context", **agg(allj, lambda d: d.client_net),
         "note": "Outside FlowGuard: operational system"},
        {"id": "completed", "label": "Agent completes job", "scope": "context", "jobs": len(allj),
         "note": "Outside FlowGuard: evidence and report captured"},
        {"id": "verification", "label": "GSC admin verifies", "scope": "context", "jobs": len(allj),
         "held": {"unverified": len(unverified), "rejected": len(rejected)},
         "note": "Unverified and rejected jobs are untouchable"},
        {"id": "verified", "label": "Verified jobs", "scope": "flowguard", **agg(verified, stake), "filter": {"verification": "VERIFIED"}},
        {"id": "lane_a", "label": "Lane A pricing and readiness", "scope": "flowguard", "jobs": len(verified),
         "exceptions": len(review) + len(blocked)},
        {"id": "ready", "label": "Ready", "scope": "flowguard", **agg(ready, lambda d: d.client_net), "filter": {"status": "READY", "invoiced": "no"}},
        {"id": "review", "label": "Needs review", "scope": "flowguard", **agg(review, stake), "filter": {"status": "NEEDS_REVIEW"}},
        {"id": "blocked", "label": "Blocked", "scope": "flowguard", **agg(blocked, stake), "filter": {"status": "BLOCKED", "verification": "VERIFIED"}},
        {"id": "lane_b", "label": "Lane B two invoices", "scope": "flowguard", **agg(invoiced, lambda d: d.client_net),
         "client_invoices": sum(1 for i in invs if i.kind == "CLIENT"), "agent_invoices": sum(1 for i in invs if i.kind == "AGENT"),
         "agent_value": str(q(sum((decs[j.id].agent_net for j in invoiced), Z))), "filter": {"invoiced": "yes"}},
        {"id": "reconciliation", "label": "Reconciliation", "scope": "flowguard", "jobs": recon["invoiced_jobs"],
         "mismatches": recon["mismatch_count"], "value": recon["generated_client_total"]},
        {"id": "export", "label": "Accounting / Sage export", "scope": "flowguard", "invoices": len(invs),
         "exported": len(exported), "not_exported": len(invs) - len(exported),
         "value": str(q(sum((i.gross for i in exported), Z)))},
    ], "leaks": dashboard(db)["leakage"], "business_date": snapmod.today().isoformat()}

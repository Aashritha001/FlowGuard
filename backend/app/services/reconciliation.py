"""Reconciliation of Lane A decisions against Lane B outputs. Any mismatch is surfaced, never hidden."""
from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from ..models import JobDecision, Invoice, InvoiceLine, Job, ReconciliationRun, InvoiceBatch
from ..engine.vat import q, totals
from .audit import log, notify

Z = Decimal("0.00")


def compute(db) -> dict:
    frozen = {d.job_id: d for d in db.scalars(select(JobDecision).where(JobDecision.frozen == True))}  # noqa
    current = {d.job_id: d for d in db.scalars(select(JobDecision).where(JobDecision.is_current == True))}  # noqa
    lines = list(db.scalars(select(InvoiceLine)))
    invoices = list(db.scalars(select(Invoice)))
    jobs = {j.id: j for j in db.scalars(select(Job))}
    by_side = {"CLIENT": Counter(), "AGENT": Counter()}
    for ln in lines:
        by_side[ln.kind][ln.job_id] += 1
    duplicates = [{"job_id": j, "side": s, "count": n} for s, c in by_side.items() for j, n in c.items() if n > 1]
    def expects(d, side):  # a £0 client side (support task inside a fixed-price job) is not invoiced
        return side == "AGENT" or q(d.client_net) != 0
    missing = [{"job_id": j, "side": s} for j, d in frozen.items() for s in ("CLIENT", "AGENT")
               if expects(d, s) and by_side[s][j] == 0]
    expected_lines = sum(1 for d in frozen.values() for s in ("CLIENT", "AGENT") if expects(d, s))
    orphans, amount_mismatch, wrong = [], [], []
    for ln in lines:
        d = frozen.get(ln.job_id)
        job = jobs.get(ln.job_id)
        if not d or d.status != "READY":
            orphans.append({"line_id": ln.id, "job_id": ln.job_id, "problem": "no READY decision behind line"})
        elif job.verification_status != "VERIFIED" or job.shadow:
            wrong.append({"line_id": ln.id, "job_id": ln.job_id, "problem": "invoiced job is not verified/live"})
        else:
            exp = d.client_net if ln.kind == "CLIENT" else d.agent_net
            if q(exp) != q(ln.net):
                amount_mismatch.append({"line_id": ln.id, "job_id": ln.job_id, "expected": str(exp), "actual": str(ln.net)})
    header = []
    for inv in invoices:
        ls = [ln.net for ln in inv.lines]
        net, vat, gross = totals(ls, Decimal(inv.vat_rate))
        if (net, vat, gross) != (q(inv.net), q(inv.vat), q(inv.gross)):
            header.append({"invoice": inv.number, "expected": [str(net), str(vat), str(gross)],
                           "actual": [str(inv.net), str(inv.vat), str(inv.gross)]})
    exp_client = q(sum((d.client_net for d in frozen.values()), Z))
    exp_agent = q(sum((d.agent_net for d in frozen.values()), Z))
    gen_client = q(sum((ln.net for ln in lines if ln.kind == "CLIENT"), Z))
    gen_agent = q(sum((ln.net for ln in lines if ln.kind == "AGENT"), Z))
    awaiting = [d for d in current.values() if d.status == "READY" and not d.frozen]
    batches = []
    for b in db.scalars(select(InvoiceBatch).order_by(InvoiceBatch.id)):
        bl = [ln for ln in lines if ln.invoice.batch_id == b.id]
        batches.append({"reference": b.reference, "expected_jobs": b.expected.get("ready_jobs"),
                        "actual_jobs": len({ln.job_id for ln in bl}),
                        "expected_client_net": b.expected.get("client_total_net"),
                        "actual_client_net": str(q(sum((ln.net for ln in bl if ln.kind == "CLIENT"), Z))),
                        "invoices": len({ln.invoice_id for ln in bl})})
    for b in batches:
        b["ok"] = b["expected_jobs"] == b["actual_jobs"] and b["expected_client_net"] == b["actual_client_net"]
    client_invs = [i for i in invoices if i.kind == "CLIENT"]
    agent_invs = [i for i in invoices if i.kind == "AGENT"]
    exp_client_invs = len({jobs[j].client_id for j in frozen if j in jobs})  # lower bound: at least one per client
    mismatches = len(duplicates) + len(missing) + len(orphans) + len(amount_mismatch) + len(header) + len(wrong) \
        + sum(1 for b in batches if not b["ok"])
    return {
        "expected_client_total": str(exp_client), "generated_client_total": str(gen_client),
        "client_difference": str(q(gen_client - exp_client)),
        "expected_agent_total": str(exp_agent), "generated_agent_total": str(gen_agent),
        "agent_difference": str(q(gen_agent - exp_agent)),
        "expected_invoice_lines": expected_lines, "generated_invoice_lines": len(lines),
        "line_difference": len(lines) - expected_lines,
        "client_invoices": len(client_invs), "agent_invoices": len(agent_invs),
        "min_expected_client_invoices": exp_client_invs,
        "invoiced_jobs": len(frozen), "ready_awaiting_invoice": len(awaiting),
        "ready_awaiting_value": str(q(sum((d.client_net for d in awaiting), Z))),
        "duplicates": duplicates, "missing": missing, "orphans": orphans, "amount_mismatches": amount_mismatch,
        "header_mismatches": header, "wrong_outputs": wrong, "batches": batches,
        "mismatch_count": mismatches,
        "wrong_output_count": len(wrong) + len(orphans) + len(amount_mismatch) + len(duplicates) + len(header),
    }


def run(db, actor: str) -> ReconciliationRun:
    res = compute(db)
    r = ReconciliationRun(run_by=actor, result=res, mismatch_count=res["mismatch_count"])
    db.add(r)
    log(db, actor, "RECONCILIATION_RUN", "reconciliation", "", new=f"{res['mismatch_count']} mismatches",
        details={k: res[k] for k in ("expected_client_total", "generated_client_total", "mismatch_count")})
    notify(db, "RECONCILIATION", "Reconciliation completed",
           f"{res['mismatch_count']} mismatches; generated client total £{res['generated_client_total']}")
    db.commit()
    return r

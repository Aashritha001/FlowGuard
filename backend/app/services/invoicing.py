"""LANE B - Two-Invoice Generator. Only READY jobs can reach this module, and it checks again itself."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, func

from ..models import Job, JobDecision, Invoice, InvoiceLine, InvoiceBatch, Client, Agent, VATConfiguration, \
    SelfBillingAgreement, Contract, RateCardVersion, Setting
from ..engine.vat import totals, agent_vat_rate, client_vat_rate, q
from . import snapshot as snapmod
from .audit import log, notify


class InvoiceSafetyError(Exception):
    pass


def eligible(db, client_id=None, verified_by=None):
    q_ = (select(Job, JobDecision).join(JobDecision, JobDecision.job_id == Job.id)
          .where(JobDecision.is_current == True, JobDecision.status == "READY", JobDecision.frozen == False,  # noqa
                 Job.shadow == False))  # noqa
    if client_id:
        q_ = q_.where(Job.client_id == client_id)
    rows = list(db.execute(q_.order_by(Job.id)))
    if verified_by:  # pay-run cut-off: only work verified on or before this date
        rows = [(j, d) for j, d in rows if j.verification_timestamp and j.verification_timestamp.date() <= verified_by]
    return rows


def _assert_safe(job: Job, dec: JobDecision):
    # Defence in depth: SC-01, SC-02, SC-05, SC-07 re-checked at the moment money is written.
    if job.verification_status != "VERIFIED":
        raise InvoiceSafetyError(f"SC-01/02: job {job.id} is {job.verification_status}")
    if dec.status != "READY" or not dec.is_current:
        raise InvoiceSafetyError(f"SC-05: job {job.id} decision is {dec.status}")
    if dec.frozen:
        raise InvoiceSafetyError(f"SC-03: job {job.id} has already been invoiced")
    if job.shadow:
        raise InvoiceSafetyError(f"SC-07: job {job.id} is a shadow-mode job")
    if dec.client_net is None or dec.agent_net is None:
        raise InvoiceSafetyError(f"job {job.id} READY without amounts")


def _next_number(db, prefix: str) -> str:
    """Sequential, never reused. Business config `invoice_numbering` can set where a series starts
    (e.g. continuing a legacy system: last used INV-00999 -> next INV-01000)."""
    n = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.number.like(f"{prefix}-%"))) or 0
    st = db.get(Setting, "invoice_numbering")
    start = int((st.value or {}).get(prefix, 1)) if st else 1
    return f"{prefix}-{start + n:05d}"


def payee_of(db, agent: Agent) -> Agent:
    """The party that is actually paid: a lead agent company for its sub-agents, otherwise the agent."""
    return db.get(Agent, agent.paid_via_agent_id) if agent.paid_via_agent_id else agent


def generate_for_jobs(db, actor: str, job_ids: list[int], issue_date=None, batch_ref=None,
                      agent_issue_date=None) -> InvoiceBatch:
    """Generates invoices for exactly the given job ids. Raises if any is not READY."""
    rows = []
    for jid in job_ids:
        job = db.get(Job, jid)
        dec = db.scalar(select(JobDecision).where(JobDecision.job_id == jid, JobDecision.is_current == True))  # noqa
        if not job or not dec:
            raise InvoiceSafetyError(f"job {jid} has no current decision")
        _assert_safe(job, dec)
        rows.append((job, dec))
    return _generate(db, actor, rows, issue_date, batch_ref, agent_issue_date)


def generate(db, actor: str, client_id=None, issue_date=None, batch_ref=None, agent_issue_date=None,
             verified_by=None) -> InvoiceBatch | None:
    rows = eligible(db, client_id, verified_by)
    if not rows:
        return None
    for job, dec in rows:
        _assert_safe(job, dec)
    return _generate(db, actor, rows, issue_date, batch_ref, agent_issue_date)


def _generate(db, actor, rows, issue_date, batch_ref, agent_issue_date=None):
    issue_date = issue_date or snapmod.today()
    agent_issue_date = agent_issue_date or issue_date
    n_batches = db.scalar(select(func.count()).select_from(InvoiceBatch)) or 0
    batch = InvoiceBatch(reference=batch_ref or f"BATCH-{issue_date:%Y%m%d}-{n_batches + 1:03d}", created_by=actor,
                         expected={"ready_jobs": len(rows),
                                   "client_total_net": str(q(sum((d.client_net for _, d in rows), Decimal("0")))),
                                   "agent_total_net": str(q(sum((d.agent_net for _, d in rows), Decimal("0"))))})
    db.add(batch)
    db.flush()
    by_client, by_agent = defaultdict(list), defaultdict(list)
    for job, dec in rows:
        if q(dec.client_net) != 0:  # a £0 client side (e.g. a support task inside a fixed-price job) has no client line
            by_client[job.client_id].append((job, dec))
        payee = payee_of(db, db.get(Agent, job.agent_id))
        vat_cfg = db.get(VATConfiguration, dec.vat_config_id)
        rate = agent_vat_rate(payee.vat_status, payee.vat_number, Decimal(vat_cfg.standard_rate),
                              payee.vat_registered_from, job.job_date)
        if rate is None:
            raise InvoiceSafetyError(f"agent {payee.code} VAT unclear but job READY")
        # one agent invoice per payee per batch, split only if the payee's VAT position changed within the period
        by_agent[(payee.id, rate)].append((job, dec))
    created = []
    for cid, items in sorted(by_client.items()):
        client = db.get(Client, cid)
        vat_cfg = db.get(VATConfiguration, items[0][1].vat_config_id)
        rate = client_vat_rate(Decimal(vat_cfg.standard_rate))
        net, vat, gross = totals([d.client_net for _, d in items], rate)
        inv = Invoice(number=_next_number(db, "INV"), kind="CLIENT", batch_id=batch.id, client_id=cid,
                      issue_date=issue_date, net=net, vat=vat, gross=gross, vat_rate=str(rate),
                      vat_treatment="Standard rated", po_refs=sorted({j.po_number for j, _ in items if j.po_number}),
                      config_snapshot=_snapshot(items, vat_cfg))
        db.add(inv)
        db.flush()
        for job, dec in items:
            db.add(InvoiceLine(invoice_id=inv.id, kind="CLIENT", job_id=job.id, decision_id=dec.id,
                               description=f"{job.job_type} {job.external_job_id} on {job.job_date:%d %b %Y}",
                               po_number=job.po_number, net=dec.client_net))
        created.append(inv)
    for (aid, rate), items in sorted(by_agent.items()):
        agent = db.get(Agent, aid)
        vat_cfg = db.get(VATConfiguration, items[0][1].vat_config_id)
        sba = db.scalar(select(SelfBillingAgreement).where(SelfBillingAgreement.agent_id == aid,
                                                           SelfBillingAgreement.status == "ACTIVE"))
        net, vat, gross = totals([d.agent_net for _, d in items], rate)
        inv = Invoice(number=_next_number(db, "SB" if agent.self_billing else "PI"), kind="AGENT", batch_id=batch.id,
                      agent_id=aid, issue_date=agent_issue_date, net=net, vat=vat, gross=gross, vat_rate=str(rate),
                      vat_treatment=("Standard rated" if rate > 0 else "Outside scope - supplier not VAT registered"),
                      self_billed=agent.self_billing, self_billing_ref=sba.reference if (sba and agent.self_billing) else None,
                      config_snapshot=_snapshot(items, vat_cfg))
        db.add(inv)
        db.flush()
        for job, dec in items:
            who = "" if job.agent_id == aid else f" by {db.get(Agent, job.agent_id).name}"
            db.add(InvoiceLine(invoice_id=inv.id, kind="AGENT", job_id=job.id, decision_id=dec.id,
                               description=f"{job.job_type} {job.external_job_id} on {job.job_date:%d %b %Y}{who}",
                               po_number=job.po_number, net=dec.agent_net))
        created.append(inv)
    for job, dec in rows:
        dec.frozen = True  # decision is now the permanent record behind the invoice
    log(db, actor, "INVOICE_BATCH_GENERATED", "invoice_batch", batch.reference,
        details={"invoices": len(created), "jobs": len(rows), **batch.expected})
    notify(db, "INVOICE_BATCH", f"Invoice batch {batch.reference} generated",
           f"{len(created)} invoices covering {len(rows)} jobs")
    for aid in {a for a, _ in by_agent}:
        notify(db, "INVOICE", "New invoice issued", f"A new invoice covering your jobs is available ({batch.reference}).", f"AGENT:{aid}")
    db.flush()
    return batch


def _snapshot(items, vat_cfg) -> dict:
    return {"rate_card_versions": sorted({d.rcv_id for _, d in items}),
            "contracts": sorted({d.contract_id for _, d in items}),
            "vat_config": {"id": vat_cfg.id, "version": vat_cfg.version, "rate": vat_cfg.standard_rate},
            "engine_versions": sorted({d.engine_version for _, d in items})}

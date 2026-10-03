"""Runs Lane A over jobs and persists decisions, reasons and exceptions."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models import now as _now
from ..models import Job, JobDecision, DecisionReason, ExceptionCase
from ..engine.pricing import price_job, ENGINE_VERSION
from ..engine import reason_codes as rc
from . import snapshot as snapmod
from .audit import log

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2}


def current_decision(db, job_id: int) -> JobDecision | None:
    return db.scalar(select(JobDecision).where(JobDecision.job_id == job_id, JobDecision.is_current == True))  # noqa


def _signature(status, cn, an, codes, rcv):
    return (status, str(cn), str(an), tuple(codes), rcv)


def process(db, actor: str = "system", job_ids: list[int] | None = None, snap=None, include_shadow=False) -> dict:
    snap = snap or snapmod.build(db, include_shadow=include_shadow)
    q = select(Job).where(Job.shadow == include_shadow)
    if job_ids:
        q = q.where(Job.id.in_(job_ids))
    jobs = list(db.scalars(q.order_by(Job.id)))
    cur = {d.job_id: d for d in db.scalars(select(JobDecision).options(selectinload(JobDecision.reasons))
                                            .where(JobDecision.is_current == True))}  # noqa
    open_ex = {e.job_id: e for e in db.scalars(select(ExceptionCase).where(ExceptionCase.state == "OPEN"))}
    changed = 0
    counts = {"READY": 0, "BLOCKED": 0, "NEEDS_REVIEW": 0}
    transitions = []
    for j in jobs:
        prev = cur.get(j.id)
        if prev and prev.frozen:
            counts[prev.status] += 1
            continue
        d = price_job(snapmod.job_facts(j), snap)
        counts[d.status] += 1
        sig = _signature(d.status, d.client_net, d.agent_net, [r.code for r in d.reasons], d.rcv_id)
        if prev and _signature(prev.status, prev.client_net, prev.agent_net,
                               [r.code for r in prev.reasons], prev.rcv_id) == sig:
            continue
        changed += 1
        if prev:
            prev.is_current = False
            if prev.status != d.status:
                transitions.append((j.id, prev.status, d.status))
        nd = JobDecision(job_id=j.id, status=d.status, client_net=d.client_net, agent_net=d.agent_net,
                         value_at_stake=d.value_at_stake, contract_id=d.contract_id, rcv_id=d.rcv_id,
                         vat_config_id=d.vat_config_id, matched_rules=d.matched_rules, trace=d.trace,
                         engine_version=ENGINE_VERSION, is_current=True)
        db.add(nd)
        db.flush()
        for r in d.reasons:
            db.add(DecisionReason(decision_id=nd.id, code=r.code, message=r.message, evidence=r.evidence,
                                  rules=r.rules, team=r.team))
        ex = open_ex.get(j.id)
        if d.status == "READY":
            if ex:
                ex.state = "RESOLVED"
                ex.resolved_at = _now()
        else:
            primary = sorted(d.reasons, key=lambda r: (r.outcome != "BLOCKED", PRIORITY_ORDER[rc.get(r.code).priority]))[0]
            if ex and ex.reason_code == primary.code:
                ex.decision_id = nd.id
                ex.value_at_stake = d.value_at_stake
            else:
                if ex:
                    ex.state = "SUPERSEDED"
                    ex.resolved_at = _now()
                db.add(ExceptionCase(job_id=j.id, decision_id=nd.id, status_kind=d.status, reason_code=primary.code,
                                     priority=rc.get(primary.code).priority, value_at_stake=d.value_at_stake,
                                     team=primary.team, context={"all_codes": [r.code for r in d.reasons],
                                                                 "pattern": _pattern(j, primary.code)}))
    if changed:
        log(db, actor, "JOBS_PROCESSED", "jobs", "", details={"processed": len(jobs), "decisions_changed": changed,
                                                              "counts": counts, "engine": ENGINE_VERSION,
                                                              "transitions": len(transitions)})
    db.commit()
    return {"processed": len(jobs), "changed": changed, "counts": counts, "transitions": transitions}


def _pattern(j: Job, code: str) -> str:
    if code == "MODIFIER_CONFLICT":
        return "+".join(c for c, f in (("WEEKEND", j.weekend), ("EMERGENCY", j.emergency), ("REVISIT", j.revisit)) if f)
    return code

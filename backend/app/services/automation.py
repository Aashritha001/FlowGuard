"""Learns from STRUCTURED human review outcomes and proposes explicit deterministic rules.
It never activates anything: a proposal goes through the normal four-eyes configuration workflow."""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from ..models import ReviewDecision, ExceptionCase, AutomationOpportunity, Job, JobDecision
from . import analytics, config_service, snapshot as snapmod
from .audit import log

MIN_SAMPLE = 10
MIN_AGREEMENT = 0.95

MATURITY = [
    {"level": 0, "name": "AI explains only", "state": "active"},
    {"level": 1, "name": "AI suggests exception resolutions", "state": "active"},
    {"level": 2, "name": "AI proposes deterministic rules (human approves)", "state": "active"},
    {"level": 3, "name": "Approved deterministic rules process matching cases automatically", "state": "active",
     "note": "The rule runs, not the AI. Level 3 is ordinary deterministic configuration."},
    {"level": "X", "name": "LLM approves financial transactions", "state": "never",
     "note": "Not implemented by design."},
]


def analyse(db) -> list[AutomationOpportunity]:
    groups = defaultdict(list)
    for r in db.scalars(select(ReviewDecision)):
        o = r.outcome or {}
        if o.get("reason_code") and o.get("resolution") and o.get("pattern"):
            groups[(o["reason_code"], o["pattern"])].append(r)
    open_ex = list(db.scalars(select(ExceptionCase).where(ExceptionCase.state == "OPEN")))
    k = analytics.dashboard(db)["kpis"]
    verified = k["verified_jobs"] or 1
    out = []
    for (code, pattern), rs in groups.items():
        if code != "MODIFIER_CONFLICT":  # only patterns that translate into an explicit rule today
            continue
        res_counts = defaultdict(int)
        for r in rs:
            res_counts[r.outcome["resolution"]] += 1
        resolution, agree = max(res_counts.items(), key=lambda x: x[1])
        if len(rs) < MIN_SAMPLE or agree / len(rs) < MIN_AGREEMENT:
            continue
        matching = [e for e in open_ex if e.reason_code == code and (e.context or {}).get("pattern") == pattern]
        span_days = max(1, (max(r.created_at for r in rs) - min(r.created_at for r in rs)).days or 1)
        monthly = round(len(rs) * 30 / max(span_days, 30)) + len(matching)
        stp_after = min(100.0, k["straight_through_rate"] + 100 * len(matching) / verified)
        opp = db.scalar(select(AutomationOpportunity).where(AutomationOpportunity.pattern == pattern,
                                                            AutomationOpportunity.reason_code == code))
        if not opp:
            opp = AutomationOpportunity(pattern=pattern, reason_code=code, resolution=resolution, sample_size=0, agreement=0)
            db.add(opp)
        if opp.status == "REJECTED":
            continue
        hi, _, lo = resolution.partition(">")
        opp.resolution, opp.sample_size, opp.agreement = resolution, len(rs), agree
        opp.review_ids = [r.id for r in rs]
        opp.open_matching = len(matching)
        opp.est_monthly_avoided = monthly
        opp.stp_before, opp.stp_after = f"{k['straight_through_rate']:.1f}", f"{stp_after:.1f}"
        opp.proposed_rule = {"if": [f"{hi.lower()} = true", f"{lo.lower()} = true"],
                             "then": f"apply {hi.lower()} uplift only", "precedence": resolution,
                             "text": f"IF {hi.lower()} = true AND {lo.lower()} = true THEN apply {hi.lower()} rate only"}
        out.append(opp)
    db.commit()
    return out


def reject(db, actor, opp_id, reason):
    o = db.get(AutomationOpportunity, opp_id)
    o.status = "REJECTED"
    log(db, actor, "AUTOMATION_SUGGESTION_REJECTED", "automation", o.id, reason=reason, ai="Suggestion from AI analysis")
    db.commit()
    return o


def create_draft_rule(db, actor, opp_id):
    o = db.get(AutomationOpportunity, opp_id)
    if o.status == "DRAFT_RULE_CREATED":
        raise config_service.ProposalError("A draft rule already exists for this opportunity")
    p = config_service.create_proposal(db, actor, "PRECEDENCE_RULE",
                                       {"client_code": "*", "rule": o.resolution,
                                        "effective_from": (snapmod.today().replace(day=1) - timedelta(days=31)).replace(day=1).isoformat()},
                                       title=f"Automation: {o.resolution.replace('>', ' supersedes ')} (learned from {o.sample_size} reviews)",
                                       source="AI", automation_id=o.id)
    o.status = "DRAFT_RULE_CREATED"
    o.proposal_id = p.id
    db.commit()
    return p

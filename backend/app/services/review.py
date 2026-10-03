"""Review Centre actions. Reviewers supply structured inputs; the deterministic engine still decides the status.
A reviewer can never set a job to READY directly."""
from datetime import datetime

from sqlalchemy import select

from ..models import now as _now
from ..models import ExceptionCase, ReviewDecision, JobOverride, Job, JobDecision
from ..engine import reason_codes as rc
from . import processing
from .audit import log, notify

# reason code -> actions a reviewer may take
ACTIONS = {
    "MULTIPLE_RATE_MATCH": ["SELECT_RULE", "REFER"],
    "MODIFIER_CONFLICT": ["APPLY_PRECEDENCE", "REFER"],
    "POSSIBLE_DUPLICATE": ["NOT_DUPLICATE", "CONFIRM_DUPLICATE", "REFER"],
    "MISSING_PO": ["ADD_PO", "REFER"],
    "MISSING_REQUIRED_DATA": ["REFER"],
    "INVALID_DATE": ["REFER"],
    "RATE_NOT_FOUND": ["REFER"],
    "CONTRACT_NOT_FOUND": ["REFER"],
    "RULE_CONFLICT": ["REFER"],
    "VAT_STATUS_UNCLEAR": ["REFER"],
    "SELF_BILLING_AGREEMENT_MISSING": ["REFER"],
    "AGENT_RATE_MISSING": ["REFER"],
    "UNKNOWN_CLIENT": ["REFER"],
    "DUPLICATE_JOB": ["ACKNOWLEDGE"],
    "DUPLICATE_INVOICE": ["ACKNOWLEDGE"],
    "UNVERIFIED_JOB": ["ACKNOWLEDGE"],
    "REJECTED_JOB": ["ACKNOWLEDGE"],
}
FIX_HINT = {
    "RATE_NOT_FOUND": "Add the missing rate through Configuration (creates a new rate-card version).",
    "CONTRACT_NOT_FOUND": "Load the contract in force for this date.",
    "RULE_CONFLICT": "Correct the overlapping rate-card versions in Configuration.",
    "VAT_STATUS_UNCLEAR": "Confirm the agent's VAT registration and number on the agent record.",
    "SELF_BILLING_AGREEMENT_MISSING": "Record a signed self-billing agreement for this agent.",
    "MISSING_REQUIRED_DATA": "Correct the job in the source system; the next sync will re-price it.",
    "INVALID_DATE": "Correct the job date in the source system.",
    "UNVERIFIED_JOB": "Immutable control: the job must be verified in the operational system first.",
    "REJECTED_JOB": "Immutable control: rejected jobs are never invoiced.",
    "DUPLICATE_INVOICE": "Immutable control: already invoiced.",
    "DUPLICATE_JOB": "Immutable control: exact duplicate of an earlier job.",
}


class ReviewError(ValueError):
    pass


def decide(db, actor: str, role: str, exception_id: int, action: str, reason: str, params: dict | None = None) -> dict:
    params = params or {}
    ex = db.get(ExceptionCase, exception_id)
    if not ex or ex.state != "OPEN":
        raise ReviewError("Exception not found or already closed")
    if not reason or len(reason.strip()) < 5:
        raise ReviewError("A review decision needs a reason (at least 5 characters)")
    allowed = ACTIONS.get(ex.reason_code, ["REFER"])
    if action not in allowed:
        raise ReviewError(f"{action} is not permitted for {ex.reason_code}. Allowed: {', '.join(allowed)}")
    job = db.get(Job, ex.job_id)
    dec = db.get(JobDecision, ex.decision_id)
    old_status = dec.status
    outcome: dict = {"reason_code": ex.reason_code, "pattern": (ex.context or {}).get("pattern", ex.reason_code)}
    override = None
    if action == "SELECT_RULE":
        cands = next((r.rules for r in dec.reasons if r.code == "MULTIPLE_RATE_MATCH"), [])
        code = params.get("rule_code")
        if code not in cands:
            raise ReviewError(f"rule_code must be one of {cands}")
        override = ("SELECT_RULE", {"rule_code": code})
        outcome["resolution"] = f"SELECT:{code}"
    elif action == "APPLY_PRECEDENCE":
        rule = str(params.get("rule", "")).upper()
        conds = next((r.evidence.get("conditions") for r in dec.reasons if r.code == "MODIFIER_CONFLICT"), []) or []
        hi, _, lo = rule.partition(">")
        if {hi, lo} != set(conds):
            raise ReviewError(f"rule must order exactly these conditions: {conds}")
        override = ("PRECEDENCE", {"rule": rule})
        outcome["resolution"] = rule
    elif action == "NOT_DUPLICATE":
        override = ("NOT_DUPLICATE", {})
        outcome["resolution"] = "NOT_DUPLICATE"
    elif action == "CONFIRM_DUPLICATE":
        override = ("CONFIRMED_DUPLICATE", {})
        outcome["resolution"] = "CONFIRMED_DUPLICATE"
    elif action == "ADD_PO":
        po = str(params.get("po_number", "")).strip()
        if not (3 <= len(po) <= 40) or not all(ch.isalnum() or ch in "-/_" for ch in po):
            raise ReviewError("po_number must be 3-40 letters, digits, - / _")
        log(db, actor, "JOB_DATA_CORRECTED", "job", job.id, old=job.po_number or "", new=po, reason=reason, role=role)
        job.po_number = po
        job.updated_at = _now()
        outcome["resolution"] = "PO_ADDED"
    else:
        outcome["resolution"] = action
    rd = ReviewDecision(exception_id=ex.id, job_id=job.id, reviewer=actor, action=action, outcome=outcome,
                        reason=reason.strip(), evidence_ref=str(params.get("evidence_ref", ""))[:80],
                        old_status=old_status, new_status=old_status)
    db.add(rd)
    db.flush()
    if override:
        val = dict(override[1], review_id=rd.id, reason=reason.strip(), reviewer=actor)
        db.add(JobOverride(job_id=job.id, kind=override[0], value=val, review_decision_id=rd.id))
    if action in ("REFER", "ACKNOWLEDGE"):
        ex.context = dict(ex.context or {}, referred_to=params.get("team") or ex.team, referred_by=actor)
        db.commit()
        log(db, actor, "EXCEPTION_REVIEWED", "job", job.id, old=old_status, new=old_status, reason=reason, role=role,
            details={"action": action, "reason_code": ex.reason_code})
        db.commit()
        return {"new_status": old_status, "review_id": rd.id}
    db.commit()
    processing.process(db, actor=actor, job_ids=[job.id])
    new = processing.current_decision(db, job.id)
    rd.new_status = new.status
    if ex.state == "OPEN" and new.status == old_status and ex.reason_code in [r.code for r in new.reasons]:
        pass
    elif ex.state == "OPEN":
        ex.state = "RESOLVED"
        ex.resolved_at = _now()
    action_name = "EXCEPTION_APPROVED" if new.status == "READY" else "EXCEPTION_REVIEWED"
    log(db, actor, action_name, "job", job.id, old=old_status, new=new.status, reason=reason, role=role,
        config_version=f"rcv:{new.rcv_id}" if new.rcv_id else "",
        ai="Explanation only" if params.get("ai_viewed") else "None",
        details={"action": action, "reason_code": ex.reason_code, "outcome": outcome})
    if job.agent_id:
        notify(db, "JOB_STATUS", f"Job {job.external_job_id} updated", f"Status is now {new.status.replace('_', ' ').lower()}.",
               f"AGENT:{job.agent_id}")
    db.commit()
    return {"new_status": new.status, "review_id": rd.id,
            "remaining_reasons": [r.code for r in new.reasons]}

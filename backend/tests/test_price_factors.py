"""Admin-defined price factors: defining one changes no price; an approved rule using it does; missing required
values hold the job for review; invoiced jobs are frozen; retiring a factor in use is refused."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Job, JobDecision, Client
from app.services import fields, config_service, processing, invoicing
from app.services.fields import FieldError
from app.services.config_service import ProposalError


def _dec(db, jid):
    return db.scalar(select(JobDecision).where(JobDecision.job_id == jid, JobDecision.is_current == True))  # noqa: E712


def _ready_job(db, client_code="CLA", job_type="PDV1"):
    cid = db.scalar(select(Client.id).where(Client.code == client_code))
    for j in db.scalars(select(Job).where(Job.client_id == cid, Job.job_type == job_type).order_by(Job.id.desc())):
        d = _dec(db, j.id)
        if d and d.status == "READY" and not d.frozen and not j.weekend and not j.emergency and not j.revisit:
            return j, d
    raise AssertionError("no READY job")


def _approve(db, payload):
    p = config_service.create_proposal(db, "finance", "RATE_CHANGE", payload)
    assert p.status == "PENDING_APPROVAL", p.conflicts
    with pytest.raises(ProposalError):
        config_service.decide(db, "finance", "FINANCE_ADMIN", p.id, "APPROVE", "maker cannot approve")
    return config_service.decide(db, "checker", "FINANCE_ADMIN", p.id, "APPROVE", "Matches client variation letter")


def test_number_factor_end_to_end(db):
    job, before = _ready_job(db)
    fields.create(db, "admin", {"label": "Floors climbed", "kind": "NUMBER", "unit": "floors"})
    assert _dec(db, job.id).client_net == before.client_net  # a definition alone never moves money

    _approve(db, {"client_code": "CLA", "job_type": "*", "effective_from": "2026-01-01",
                  "modifiers": [{"factor": "floors_climbed", "client_amount": "2.50", "agent_amount": "1.50",
                                 "included_units": 2}]})
    r = fields.set_job_values(db, "reviewer", "REVIEWER", job.id, {"floors_climbed": "6"}, "Agent photo of stairwell")
    d = _dec(db, job.id)
    assert r["status"] == "READY"
    assert d.client_net == before.client_net + Decimal("10.00")  # (6 - 2 free) x £2.50
    assert d.agent_net == before.agent_net + Decimal("6.00")
    node = next(n for n in d.trace if n["label"].startswith("Price factor"))
    assert node["detail"]["units"] == "4"

    with pytest.raises(FieldError):  # in use by an active rule
        fields.retire(db, "admin", "floors_climbed", "no longer needed")


def test_required_factor_holds_job_until_value_given(db):
    job, before = _ready_job(db)
    fields.create(db, "admin", {"label": "Access type", "kind": "CHOICE", "choices": "Standard, Gated estate, High rise",
                                "required_for": ["PDV1"]})
    d = _dec(db, job.id)
    assert d.status == "NEEDS_REVIEW" and d.reasons[0].code == "MISSING_REQUIRED_DATA"
    assert "Access type" in d.reasons[0].message
    with pytest.raises(FieldError):
        fields.set_job_values(db, "reviewer", "REVIEWER", job.id, {"access_type": "Castle"}, "from agent app")
    _approve(db, {"client_code": "CLA", "job_type": "PDV1", "effective_from": "2026-01-01",
                  "modifiers": [{"factor": "access_type", "choice": "gated estate", "client_amount": "4.00", "agent_amount": "2.00"}]})
    fields.set_job_values(db, "reviewer", "REVIEWER", job.id, {"access_type": "Gated estate"}, "from agent app")
    d = _dec(db, job.id)
    assert d.status == "READY" and d.client_net == before.client_net + Decimal("4.00")
    fields.set_job_values(db, "reviewer", "REVIEWER", job.id, {"access_type": "standard"}, "corrected by agent")
    assert _dec(db, job.id).client_net == before.client_net


def test_invoiced_job_is_frozen_and_rules_need_known_factors(db):
    fields.create(db, "admin", {"label": "Parking permit", "kind": "BOOLEAN"})
    job, _ = _ready_job(db)
    invoicing.generate_for_jobs(db, "finance", [job.id])
    db.commit()
    with pytest.raises(FieldError):
        fields.set_job_values(db, "finance", "FINANCE_ADMIN", job.id, {"parking_permit": True}, "late info")
    with pytest.raises(ProposalError):
        config_service.create_proposal(db, "finance", "RATE_CHANGE", {
            "client_code": "CLA", "job_type": "*", "effective_from": "2026-01-01",
            "modifiers": [{"factor": "no_such_factor", "client_amount": "1.00"}]})
    with pytest.raises(ProposalError):  # a base rate cannot target "all job types"
        config_service.create_proposal(db, "finance", "RATE_CHANGE", {
            "client_code": "CLA", "job_type": "*", "client_amount": "50.00", "effective_from": "2026-01-01"})

"""Builds an engine Snapshot from the database. The engine itself never touches the DB."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from ..config import settings
from ..models import now as _now
from ..models import (Client, Agent, Contract, RateCardVersion, RateCard, VATConfiguration,
                      SelfBillingAgreement, Job, InvoiceLine, Invoice, JobOverride, Setting, PriceField)
from ..engine.types import (Snapshot, ClientFacts, AgentFacts, ContractFacts, RCVFacts, RuleFacts,
                            VATFacts, SBAFacts, JobFacts, FieldFacts)
from ..engine.pricing import fuzzy_key


def today() -> date:
    if settings.business_date:
        return date.fromisoformat(settings.business_date)
    return _now().date()


def job_ref(j) -> str:
    return f"FG-{j.id:05d}"


def job_facts(j: Job) -> JobFacts:
    return JobFacts(id=j.id, ref=job_ref(j), external_job_id=j.external_job_id, source_system=j.source_system,
                    client_id=j.client_id, agent_id=j.agent_id, job_type=j.job_type, job_date=j.job_date,
                    verification_status=j.verification_status, verified_by=j.verified_by, weekend=j.weekend,
                    emergency=j.emergency, revisit=j.revisit, po_number=j.po_number, postcode=j.postcode,
                    created_order=j.id, minutes_on_site=j.minutes_on_site, attributes=dict(j.attributes or {}))


def rcv_facts(v: RateCardVersion) -> RCVFacts:
    return RCVFacts(id=v.id, rate_card_id=v.rate_card_id, rate_card_name=v.rate_card.name, client_id=v.rate_card.client_id,
                    version=v.version, valid_from=v.valid_from, valid_to=v.valid_to, status=v.status,
                    precedence=list(v.precedence or []),
                    rules=[RuleFacts(id=r.id, code=r.rule_code, kind=r.kind, job_type=r.job_type, condition=r.condition,
                                     group=r.group, client_amount=r.client_amount, agent_amount=r.agent_amount,
                                     clause_ref=r.clause_ref, agent_code=r.agent_code, included_minutes=r.included_minutes,
                                     agent_extra_per_30=r.agent_extra_per_30, included_units=r.included_units)
                           for r in v.rules])


def active_fields(db, include_retired: bool = False) -> dict:
    """Price factors the engine knows. Reconstruction includes retired ones (never required) so historical
    invoices that used a since-retired factor still reproduce."""
    q_ = select(PriceField) if include_retired else select(PriceField).where(PriceField.status == "ACTIVE")
    return {f.key: FieldFacts(f.key, f.label, f.kind, list(f.choices or []),
                              list(f.required_for or []) if f.status == "ACTIVE" else [])
            for f in db.scalars(q_)}


def sba_policy(db) -> str:
    st = db.get(Setting, "self_billing_policy")
    return (st.value or {}).get("sba_required_for", "ALL") if st else "ALL"


def build(db, include_shadow: bool = False, rcv_status: tuple = ("ACTIVE",)) -> Snapshot:
    clients = {c.id: ClientFacts(c.id, c.code, c.name, c.po_required) for c in db.scalars(select(Client))}
    agents = {a.id: AgentFacts(a.id, a.code, a.name, a.supplier_type, a.vat_status, a.vat_number, a.self_billing,
                               a.paid_via_agent_id, a.vat_registered_from)
              for a in db.scalars(select(Agent))}
    contracts = [ContractFacts(c.id, c.client_id, c.code, c.version, c.valid_from, c.valid_to, c.status)
                 for c in db.scalars(select(Contract))]
    rcvs = [rcv_facts(v) for v in db.scalars(select(RateCardVersion).where(RateCardVersion.status.in_(rcv_status)))]
    vats = [VATFacts(v.id, v.version, Decimal(v.standard_rate), v.valid_from, v.valid_to, v.status)
            for v in db.scalars(select(VATConfiguration))]
    sbas = [SBAFacts(s.agent_id, s.reference, s.valid_from, s.valid_to, s.status) for s in db.scalars(select(SelfBillingAgreement))]
    snap = Snapshot(today=today(), clients=clients, agents=agents, contracts=contracts, rcvs=rcvs, vats=vats, sbas=sbas,
                    sba_required_for=sba_policy(db), fields=active_fields(db))
    for jid, num in db.execute(select(InvoiceLine.job_id, Invoice.number).join(Invoice).where(InvoiceLine.kind == "CLIENT")):
        snap.invoiced[jid] = num
    for o in db.scalars(select(JobOverride).order_by(JobOverride.id)):
        snap.overrides.setdefault(o.job_id, []).append({"kind": o.kind, "value": o.value})
    q = select(Job) if include_shadow else select(Job).where(Job.shadow == False)  # noqa: E712
    for j in db.scalars(q):
        index_job(snap, j)
    return snap


def index_job(snap: Snapshot, j: Job) -> None:
    f = job_facts(j)
    snap.exact_index.setdefault((f.source_system, f.external_job_id), []).append((f.created_order, f.id, f.ref))
    if f.postcode and f.verification_status == "VERIFIED":
        snap.fuzzy_index.setdefault(fuzzy_key(f), []).append((f.created_order, f.id, f.ref, f.external_job_id))

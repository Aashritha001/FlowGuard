"""Historical reconstruction: re-run the deterministic engine using exactly the configuration versions recorded
on an invoice's decisions, regardless of what is configured today."""
from decimal import Decimal

from sqlalchemy import select

from ..models import Invoice, JobDecision, Job, RateCardVersion, Contract, VATConfiguration, JobOverride, ReviewDecision
from ..engine.pricing import price_job
from ..engine.types import Snapshot, ContractFacts, VATFacts, SBAFacts
from ..engine.vat import totals, q
from . import snapshot as snapmod


def reproduce(db, invoice_id: int) -> dict:
    inv = db.get(Invoice, invoice_id)
    base = snapmod.build(db)
    steps, ok = [], True
    for ln in inv.lines:
        dec = db.get(JobDecision, ln.decision_id)
        job = db.get(Job, ln.job_id)
        rcv = db.get(RateCardVersion, dec.rcv_id)
        con = db.get(Contract, dec.contract_id)
        vat = db.get(VATConfiguration, dec.vat_config_id)
        rf = snapmod.rcv_facts(rcv)
        rc_node = next((n for n in dec.trace if n["key"] == "rate_card"), {})
        # Rules of a version are immutable. Validity window is taken from the decision record.
        from datetime import date as _d
        vf = rc_node.get("detail", {}).get("valid_from")
        vt = rc_node.get("detail", {}).get("valid_to")
        rf.valid_from = _d.fromisoformat(vf) if vf and vf != "-" else rf.valid_from
        rf.valid_to = _d.fromisoformat(vt) if vt and vt != "-" else None
        rf.status = "ACTIVE"
        snap = Snapshot(today=job.job_date, clients=base.clients, agents=base.agents,
                        contracts=[ContractFacts(con.id, con.client_id, con.code, con.version, con.valid_from, None, "ACTIVE")],
                        rcvs=[rf], vats=[VATFacts(vat.id, vat.version, Decimal(vat.standard_rate), vat.valid_from, None, "ACTIVE")],
                        sbas=[SBAFacts(a.agent_id, a.reference, a.valid_from, a.valid_to, "ACTIVE") for a in base.sbas],
                        sba_required_for=base.sba_required_for,
                        fields=snapmod.active_fields(db, include_retired=True))
        # only human resolutions that existed when the invoice was issued
        ovs = []
        for o in db.scalars(select(JobOverride).where(JobOverride.job_id == job.id).order_by(JobOverride.id)):
            rd = db.get(ReviewDecision, o.review_decision_id) if o.review_decision_id else None
            if o.created_at <= inv.created_at or (rd and rd.created_at <= inv.created_at):
                ovs.append({"kind": o.kind, "value": o.value})
        snap.overrides = {job.id: ovs}
        d = price_job(snapmod.job_facts(job), snap)
        got = d.client_net if ln.kind == "CLIENT" else d.agent_net
        match = d.status == "READY" and got is not None and q(got) == q(ln.net)
        ok = ok and match
        steps.append({"job_id": job.id, "ref": snapmod.job_ref(job), "external_id": job.external_job_id,
                      "contract": f"{con.code} v{con.version}", "rate_card": f"{rcv.rate_card.name} v{rcv.version}",
                      "rate_card_status_today": rcv.status, "rules": d.matched_rules, "vat_config": f"v{vat.version}",
                      "recorded": str(ln.net), "reproduced": str(got) if got is not None else None, "match": match,
                      "trace": d.trace})
    net, vat_amt, gross = totals([Decimal(s["reproduced"]) if s["reproduced"] else Decimal("0") for s in steps],
                                 Decimal(inv.vat_rate))
    header_ok = (net, vat_amt, gross) == (q(inv.net), q(inv.vat), q(inv.gross))
    return {"invoice": inv.number, "kind": inv.kind, "recorded": {"net": str(inv.net), "vat": str(inv.vat), "gross": str(inv.gross)},
            "reproduced": {"net": str(net), "vat": str(vat_amt), "gross": str(gross)},
            "vat_rate": inv.vat_rate, "lines": steps, "result": "REPRODUCED SUCCESSFULLY" if ok and header_ok else "MISMATCH",
            "config_snapshot": inv.config_snapshot}

"""LANE A - deterministic Price & Readiness Engine.

Pure function of (JobFacts, Snapshot). Same inputs always give the same Decision.
No AI, no network, no database access, no randomness, no floating point.

Outcome is always exactly one of READY / BLOCKED / NEEDS_REVIEW. There is no "probably okay".
"""
from datetime import date
from decimal import Decimal

from . import reason_codes as rc
from .types import JobFacts, Snapshot, Decision, Reason, RuleFacts
from .vat import q, agent_vat_rate, client_vat_rate
from . import factors

ENGINE_VERSION = "1.1.0"
EARLIEST_SUPPORTED_DATE = date(2020, 1, 1)
STATUSES = ("READY", "BLOCKED", "NEEDS_REVIEW")

# Immutable system safety controls. These are code, not configuration: no admin screen, API, or AI
# tool can switch them off. They are listed here so the UI can show them verbatim.
SAFETY_CONTROLS = [
    {"id": "SC-01", "name": "Unverified job cannot be invoiced", "enforced_in": "pricing.price_job, invoicing.generate"},
    {"id": "SC-02", "name": "Rejected job cannot be invoiced", "enforced_in": "pricing.price_job, invoicing.generate"},
    {"id": "SC-03", "name": "Duplicate invoice protection", "enforced_in": "pricing.price_job + DB unique(job_id, kind)"},
    {"id": "SC-04", "name": "Exactly one processing state per job", "enforced_in": "pricing._finish + DB CHECK + current-decision index"},
    {"id": "SC-05", "name": "Invoices only for READY jobs", "enforced_in": "invoicing.generate (re-checks at write time)"},
    {"id": "SC-06", "name": "Every financial decision is auditable", "enforced_in": "append-only audit_events (DB trigger)"},
    {"id": "SC-07", "name": "Shadow-mode jobs can never be invoiced", "enforced_in": "invoicing.generate"},
    {"id": "SC-08", "name": "AI has no financial write authority", "enforced_in": "ai.gateway tool allow-list"},
    {"id": "SC-09", "name": "Similarity never decides a duplicate on its own", "enforced_in": "pricing._possible_duplicate -> NEEDS_REVIEW"},
    {"id": "SC-10", "name": "Missing financial data is never invented", "enforced_in": "pricing.price_job -> NEEDS_REVIEW"},
]


def _covers(valid_from: date, valid_to: date | None, d: date) -> bool:
    return valid_from <= d and (valid_to is None or d <= valid_to)


def _fmt(d: date | None) -> str:
    return d.isoformat() if d else "-"


class _Run:
    def __init__(self, job: JobFacts, snap: Snapshot):
        self.job, self.snap = job, snap
        self.trace: list[dict] = []
        self.reasons: list[Reason] = []
        self.matched: list[str] = []
        self.client_net = self.agent_net = self.at_stake = None
        self.contract_id = self.rcv_id = self.vat_id = None
        self.facts = {"job_ref": job.ref, "job_date": _fmt(job.job_date), "job_type": job.job_type or "-",
                      "verified_by": job.verified_by or "-"}

    def node(self, key, label, value, state="ok", detail=None, ref=None):
        self.trace.append({"key": key, "label": label, "value": value, "state": state,
                           "detail": detail or {}, "ref": ref})

    def reason(self, code, evidence=None, rule_refs=None, **facts):
        d = rc.get(code)
        self.facts.update(facts)
        self.reasons.append(Reason(code, rc.render(code, self.facts), evidence or {}, rule_refs or [], d.team, d.outcome))

    def finish(self) -> Decision:
        if any(r.outcome == "BLOCKED" for r in self.reasons):
            status = "BLOCKED"
        elif self.reasons:
            status = "NEEDS_REVIEW"
        else:
            status = "READY"
        assert status in STATUSES  # SC-04
        if status == "READY":
            # Invariant: a READY decision always has both sides priced from a matched rule.
            assert self.client_net is not None and self.agent_net is not None and self.rcv_id and self.matched
        self.node("readiness", "Readiness", status, "ok" if status == "READY" else "fail",
                  {"reason_codes": [r.code for r in self.reasons], "engine_version": ENGINE_VERSION})
        stake = self.at_stake if self.at_stake is not None else self.client_net
        return Decision(self.job.id, status, self.client_net, self.agent_net, stake,
                        self.contract_id, self.rcv_id, self.vat_id, self.matched, self.trace, self.reasons)


def price_job(job: JobFacts, snap: Snapshot) -> Decision:
    r = _Run(job, snap)
    s = snap
    r.node("job", "Job", job.ref, "info", {"external_id": job.external_job_id, "source": job.source_system,
                                          "type": job.job_type, "date": _fmt(job.job_date),
                                          "weekend": job.weekend, "emergency": job.emergency, "revisit": job.revisit})

    # ---- SC-01 / SC-02 verification gate (immutable) -------------------------------------
    if job.verification_status == "REJECTED":
        r.node("verification", "Verification", "Rejected", "fail", {"by": job.verified_by})
        r.reason("REJECTED_JOB")
        return r.finish()
    if job.verification_status != "VERIFIED":
        r.node("verification", "Verification", job.verification_status.title(), "fail")
        r.reason("UNVERIFIED_JOB")
        return r.finish()
    r.node("verification", "Verification", "Verified", "ok", {"by": job.verified_by})

    # ---- SC-03 duplicate invoice / exact duplicate ----------------------------------------
    if job.id in s.invoiced:
        r.node("duplicate", "Duplicate check", "Already invoiced", "fail", {"invoice": s.invoiced[job.id]})
        r.reason("DUPLICATE_INVOICE", {"invoice": s.invoiced[job.id]}, invoice_no=s.invoiced[job.id])
        return r.finish()
    copies = sorted(s.exact_index.get((job.source_system, job.external_job_id), []))
    if copies and copies[0][1] != job.id:
        first = copies[0]
        if first[1] in s.invoiced:  # re-submission of a job that has already been invoiced
            r.node("duplicate", "Duplicate check", "Re-submitted invoiced job", "fail",
                   {"first_copy": first[2], "invoice": s.invoiced[first[1]]})
            r.reason("DUPLICATE_INVOICE", {"first_copy_job": first[2], "invoice": s.invoiced[first[1]]},
                     invoice_no=s.invoiced[first[1]])
            return r.finish()
        r.node("duplicate", "Duplicate check", "Exact duplicate", "fail", {"first_copy": first[2]})
        r.reason("DUPLICATE_JOB", {"first_copy_job": first[2], "external_id": job.external_job_id}, other_ref=first[2])
        return r.finish()

    # ---- required data (never invented) ----------------------------------------------------
    missing = [n for n, v in (("client", job.client_id), ("agent", job.agent_id),
                              ("job type", job.job_type), ("job date", job.job_date)) if not v]
    if job.agent_id and job.agent_id not in s.agents:
        missing.append("agent (not configured)")
    missing += factors.missing_or_invalid(s.fields, job.job_type, job.attributes)  # admin-defined price factors
    if missing:
        r.node("data", "Required data", "Missing", "fail", {"missing": missing})
        r.reason("MISSING_REQUIRED_DATA", {"missing": missing}, fields=", ".join(missing))
        return r.finish()
    if job.job_date > s.today:
        r.reason("INVALID_DATE", {"job_date": _fmt(job.job_date), "today": _fmt(s.today)}, problem="in the future")
        r.node("data", "Job date", _fmt(job.job_date), "fail")
        return r.finish()
    if job.job_date < EARLIEST_SUPPORTED_DATE:
        r.reason("INVALID_DATE", {"job_date": _fmt(job.job_date)}, problem="earlier than any configured contract")
        r.node("data", "Job date", _fmt(job.job_date), "fail")
        return r.finish()

    client = s.clients.get(job.client_id)
    agent = s.agents[job.agent_id]
    r.facts.update(agent_name=agent.name)
    if not client:
        r.node("client", "Client", "Unknown", "fail")
        r.reason("UNKNOWN_CLIENT", {"client_id": job.client_id})
        return r.finish()
    r.facts.update(client_name=client.name)
    r.node("client", "Client", client.name, "ok", {"code": client.code, "po_required": client.po_required})

    # ---- contract in force on job date -----------------------------------------------------
    cons = [c for c in s.contracts if c.client_id == client.id and c.status == "ACTIVE"
            and _covers(c.valid_from, c.valid_to, job.job_date)]
    if not cons:
        r.node("contract", "Contract", "None in force", "fail")
        r.reason("CONTRACT_NOT_FOUND")
        return r.finish()
    if len(cons) > 1:
        r.node("contract", "Contract", "Overlapping", "fail", {"versions": [f"{c.code} v{c.version}" for c in cons]})
        r.reason("RULE_CONFLICT", {"contracts": [c.id for c in cons]}, versions=", ".join(f"{c.code} v{c.version}" for c in cons))
        return r.finish()
    con = cons[0]
    r.contract_id = con.id
    r.node("contract", "Contract", f"{con.code} v{con.version}", "ok",
           {"valid_from": _fmt(con.valid_from), "valid_to": _fmt(con.valid_to)}, f"contract:{con.id}")

    # ---- rate-card version by effective date -----------------------------------------------
    rcvs = [v for v in s.rcvs if v.client_id == client.id and v.status == "ACTIVE"
            and _covers(v.valid_from, v.valid_to, job.job_date)]
    if not rcvs:
        r.node("rate_card", "Rate card", "None in force", "fail")
        r.reason("RATE_NOT_FOUND", rate_card="none in force")
        return r.finish()
    if len(rcvs) > 1:
        names = ", ".join(f"{v.rate_card_name} v{v.version}" for v in rcvs)
        r.node("rate_card", "Rate card", "Overlapping versions", "fail", {"versions": names})
        r.reason("RULE_CONFLICT", {"rcv_ids": [v.id for v in rcvs]}, versions=names)
        return r.finish()
    rcv = rcvs[0]
    r.rcv_id = rcv.id
    r.facts.update(rate_card=f"{rcv.rate_card_name} v{rcv.version}")
    r.node("rate_card", "Rate card", f"{rcv.rate_card_name} v{rcv.version}", "ok",
           {"valid_from": _fmt(rcv.valid_from), "valid_to": _fmt(rcv.valid_to), "precedence": rcv.precedence},
           f"rcv:{rcv.id}")

    # ---- base rule ------------------------------------------------------------------------
    overrides = s.overrides.get(job.id, [])
    bases = [x for x in rcv.rules if x.kind == "BASE" and x.job_type == job.job_type]
    sel = next((o for o in overrides if o["kind"] == "SELECT_RULE"), None)
    if sel and len(bases) > 1:
        chosen = [b for b in bases if b.code == sel["value"].get("rule_code")]
        if chosen:
            bases = chosen
            r.node("review_override", "Reviewer selection", sel["value"]["rule_code"], "info",
                   {"review_decision": sel["value"].get("review_id"), "reason": sel["value"].get("reason")})
    if not bases:
        r.node("base_rule", "Base rate", "No rule", "fail", {"job_type": job.job_type})
        r.reason("RATE_NOT_FOUND")
        return r.finish()
    if len(bases) > 1:
        codes = [b.code for b in bases]
        r.node("base_rule", "Base rate", "Multiple matches", "fail",
               {"candidates": [{"rule": b.code, "client": str(b.client_amount)} for b in bases]})
        r.at_stake = max(q(b.client_amount) for b in bases if b.client_amount is not None)
        r.reason("MULTIPLE_RATE_MATCH", {"candidates": codes}, codes, rules=", ".join(codes))
        return r.finish()
    base = bases[0]
    if base.agent_amount is None or base.client_amount is None:
        r.reason("AGENT_RATE_MISSING", rules=base.code)
        r.node("base_rule", "Base rate", base.code, "fail")
        return r.finish()
    r.matched.append(base.code)
    r.node("base_rule", f"Matched rule {base.code}", f"£{q(base.client_amount)}", "ok",
           {"rule": base.code, "client_amount": str(q(base.client_amount)), "agent_amount": str(q(base.agent_amount)),
            "clause": base.clause_ref}, f"rule:{base.id}")
    client_net, agent_net = q(base.client_amount), q(base.agent_amount)
    pay_rule = base

    # ---- special agent rate (replaces the standard agent rate for that agent or its payee) ----
    payee = s.agents.get(agent.paid_via_id) if agent.paid_via_id else agent
    if payee is None:
        r.node("payee", "Payee", "Unknown lead company", "fail", {"paid_via_id": agent.paid_via_id})
        r.reason("MISSING_REQUIRED_DATA", {"missing": ["payee (paid-via agent not configured)"]},
                 fields="payee (paid-via agent not configured)")
        return r.finish()
    if payee is not agent:
        r.node("payee", "Paid via", payee.name, "info", {"agent": agent.code, "payee": payee.code})
    specials = [x for x in rcv.rules if x.kind == "AGENT_RATE" and x.job_type == job.job_type
                and x.agent_code in (agent.code, payee.code)]
    if specials:
        own = [x for x in specials if x.agent_code == agent.code]  # the agent's own rate beats its payee's
        specials = own or specials
        if len(specials) > 1:
            codes = [x.code for x in specials]
            r.node("agent_rate", "Special agent rate", "Multiple matches", "fail", {"candidates": codes})
            r.reason("MULTIPLE_RATE_MATCH", {"candidates": codes}, codes, rules=", ".join(codes))
            return r.finish()
        pay_rule = specials[0]
        agent_net = q(pay_rule.agent_amount)
        r.matched.append(pay_rule.code)
        r.node("agent_rate", f"Special agent rate {pay_rule.code}", f"£{agent_net}", "ok",
               {"rule": pay_rule.code, "agent_code": pay_rule.agent_code, "replaces": str(q(base.agent_amount)),
                "clause": pay_rule.clause_ref}, f"rule:{pay_rule.id}")

    # ---- time-based agent pay: first N minutes, then per 30 minutes or part of 30 minutes ------
    if pay_rule.included_minutes is not None and pay_rule.agent_extra_per_30 is not None:
        if job.minutes_on_site is None:
            r.node("time", "Time on site", "Missing", "fail", {"rule": pay_rule.code})
            r.reason("MISSING_REQUIRED_DATA", {"missing": ["time on site"]}, fields="time on site")
            return r.finish()
        over = max(0, job.minutes_on_site - pay_rule.included_minutes)
        blocks = -(-over // 30)  # ceiling: a part of 30 minutes counts as a full block
        extra = q(pay_rule.agent_extra_per_30 * blocks)
        agent_net += extra
        r.node("time", "Time on site", f"{job.minutes_on_site} min", "ok",
               {"included_minutes": pay_rule.included_minutes, "extra_blocks": blocks,
                "per_30": str(q(pay_rule.agent_extra_per_30)), "extra": str(extra)})

    # ---- modifiers -------------------------------------------------------------------------
    flags = {"WEEKEND": job.weekend, "EMERGENCY": job.emergency, "REVISIT": job.revisit}
    units: dict[int, Decimal] = {}  # rule id -> multiplier for NUMBER price factors
    mods = []
    for m in rcv.rules:
        if m.kind != "MODIFIER" or m.job_type not in (job.job_type, "*") or not m.condition:
            continue
        if factors.is_factor(m.condition):
            applies, n, problem = factors.evaluate(m.condition, m.included_units, s.fields, job.attributes)
            if problem:
                r.node("modifier", f"Modifier {m.code}", "Undefined price factor", "fail", {"condition": m.condition})
                r.reason("RULE_CONFLICT", {"rule": m.code, "problem": problem}, [m.code], versions=f"{m.code}: {problem}")
                return r.finish()
            if applies:
                units[m.id] = n
                mods.append(m)
        elif flags.get(m.condition):
            mods.append(m)
    specific = {m.condition for m in mods if m.job_type == job.job_type}
    mods = [m for m in mods if m.job_type != "*" or m.condition not in specific]  # specific shadows generic
    applied: list[RuleFacts] = []
    groups: dict[str, list[RuleFacts]] = {}
    for m in mods:
        if m.group:
            groups.setdefault(m.group, []).append(m)
        else:
            applied.append(m)
    precedence = list(rcv.precedence) + [o["value"]["rule"] for o in overrides if o["kind"] == "PRECEDENCE"]
    for g, ms in groups.items():
        if len(ms) == 1:
            applied.append(ms[0])
            continue
        winner = None
        for p in precedence:  # "EMERGENCY>WEEKEND"
            hi, _, lo = p.partition(">")
            conds = {m.condition for m in ms}
            if hi in conds and lo in conds and len(conds) == 2:
                winner = next(m for m in ms if m.condition == hi)
                r.node("precedence", "Precedence rule", p, "ok", {"group": g, "source": "rate card" if p in rcv.precedence else "reviewer"})
                break
        if winner:
            applied.append(winner)
        else:
            codes = [m.code for m in ms]
            r.node("modifier", "Modifiers", "Conflict", "fail", {"group": g, "candidates": codes})
            r.at_stake = client_net + max(q(m.client_amount) for m in ms)
            r.reason("MODIFIER_CONFLICT", {"group": g, "conditions": sorted(m.condition for m in ms)}, codes,
                     rules=" and ".join(codes))
            return r.finish()
    for m in sorted(applied, key=lambda x: x.code):
        r.matched.append(m.code)
        n = units.get(m.id, Decimal(1))
        c_add, a_add = q(m.client_amount * n), q(m.agent_amount * n)
        client_net += c_add
        agent_net += a_add
        detail = {"condition": m.condition, "client_amount": str(c_add), "agent_amount": str(a_add), "clause": m.clause_ref}
        label = f"Modifier {m.code}"
        if factors.is_factor(m.condition):
            key, choice = factors.split(m.condition)
            fld = s.fields[key]
            label = f"Price factor: {fld.label}" + (f" = {choice}" if choice else "")
            detail.update(factor=key, value=(job.attributes or {}).get(key), rule=m.code)
            if fld.kind == "NUMBER":
                detail.update(units=str(n), per_unit_client=str(q(m.client_amount)), per_unit_agent=str(q(m.agent_amount)),
                              included_units=m.included_units or 0)
        r.node("modifier", label, f"+£{c_add}", "ok", detail, f"rule:{m.id}")
    r.client_net, r.agent_net = q(client_net), q(agent_net)
    r.node("net", "Client net", f"£{r.client_net}", "ok", {"agent_net": str(r.agent_net),
                                                          "margin": str(q(r.client_net - r.agent_net))})

    # ---- readiness checks (all collected) ----------------------------------------------------
    if client.po_required:
        if job.po_number:
            r.node("po", "Purchase order", job.po_number, "ok")
        else:
            r.node("po", "Purchase order", "Missing", "fail", {"required_by": client.name})
            r.reason("MISSING_PO", {"po_required": True})
    a_rate = None
    vats = [v for v in s.vats if v.status == "ACTIVE" and _covers(v.valid_from, v.valid_to, job.job_date)]
    if len(vats) != 1:
        r.node("vat", "VAT configuration", "Not uniquely defined", "fail")
        r.reason("VAT_STATUS_UNCLEAR", vat_status="not configured", vat_detail=" (no single VAT configuration in force)")
    else:
        vc = vats[0]
        r.vat_id = vc.id
        a_rate = agent_vat_rate(payee.vat_status, payee.vat_number, vc.standard_rate, payee.vat_registered_from, job.job_date)
        r.node("vat", f"VAT config v{vc.version}", f"Sales {client_vat_rate(vc.standard_rate) * 100:.0f}%", "ok" if a_rate is not None else "fail",
               {"client_rate": str(client_vat_rate(vc.standard_rate)),
                "agent_rate": None if a_rate is None else str(a_rate),
                "agent_vat_status": payee.vat_status, "payee": payee.code,
                "vat_registered_from": _fmt(payee.vat_registered_from)}, f"vat:{vc.id}")
        if a_rate is None:
            detail = " but no VAT number is held" if payee.vat_status == "REGISTERED" else ""
            r.reason("VAT_STATUS_UNCLEAR", {"vat_status": payee.vat_status, "vat_number": bool(payee.vat_number)},
                     vat_status=payee.vat_status.replace("_", " ").lower(), vat_detail=detail)
    needs_sba = payee.self_billing and (s.sba_required_for == "ALL" or a_rate is None or a_rate > 0)
    if payee.self_billing and not needs_sba:
        r.node("self_billing", "Self-billing agreement", "Not required (payee not VAT registered on job date)", "ok",
               {"policy": s.sba_required_for})
    if needs_sba:
        ok = [a for a in s.sbas if a.agent_id == payee.id and a.status == "ACTIVE" and _covers(a.valid_from, a.valid_to, job.job_date)]
        if ok:
            r.node("self_billing", "Self-billing agreement", ok[0].reference, "ok")
        else:
            r.node("self_billing", "Self-billing agreement", "Missing", "fail")
            r.reason("SELF_BILLING_AGREEMENT_MISSING")
    _possible_duplicate(r, job, s, overrides)
    return r.finish()


def _norm_pc(pc: str | None) -> str:
    return "".join((pc or "").upper().split())


def fuzzy_key(job: JobFacts):
    return (job.agent_id, job.job_date, _norm_pc(job.postcode), job.job_type)


def _possible_duplicate(r: _Run, job: JobFacts, s: Snapshot, overrides: list[dict]) -> None:
    """SC-09: similarity is a signal for a human, never an automatic duplicate verdict."""
    if not job.postcode:
        return
    # exact copies (same external ID) are handled by DUPLICATE_JOB, so they are excluded here
    others = [e for e in s.fuzzy_index.get(fuzzy_key(job), []) if e[1] != job.id and e[3] != job.external_job_id]
    if not others:
        r.node("duplicate", "Duplicate check", "No match", "ok")
        return
    if any(o["kind"] == "CONFIRMED_DUPLICATE" for o in overrides):
        r.node("duplicate", "Duplicate check", "Confirmed duplicate by reviewer", "fail")
        r.reason("DUPLICATE_JOB", {"similar_to": [e[2] for e in others], "confirmed_by_review": True}, other_ref=others[0][2])
        return
    if any(o["kind"] == "NOT_DUPLICATE" for o in overrides):
        r.node("duplicate", "Duplicate check", "Cleared by reviewer", "ok", {"similar_to": [e[2] for e in others]})
        return
    r.node("duplicate", "Duplicate check", "Possible duplicate", "fail", {"similar_to": [e[2] for e in others]})
    r.reason("POSSIBLE_DUPLICATE", {"similar_to": [e[2] for e in others],
                                    "signals": ["agent", "date", "postcode", "job type"]}, other_ref=others[0][2])

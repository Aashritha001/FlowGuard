"""Versioned configuration governance: proposals -> validation -> conflict check -> impact simulation ->
four-eyes approval -> new immutable version. History is never overwritten; rollback creates a new version."""
import copy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from ..models import (ConfigProposal, ConfigApproval, RateCard, RateCardVersion, PricingRule, Client, Job,
                      JobDecision, Setting, PriceField)
from ..engine import factors
from ..engine.types import RCVFacts, RuleFacts
from ..engine.conflicts import detect
from ..engine.pricing import price_job
from ..engine.vat import q
from . import snapshot as snapmod
from . import processing
from .audit import log, notify

KINDS = ("RATE_CHANGE", "PRECEDENCE_RULE", "ROLLBACK", "PO_REQUIREMENT")
CONDITIONS = ("WEEKEND", "EMERGENCY", "REVISIT")


class ProposalError(ValueError):
    pass


# ------------------------------------------------------------------ schema validation
def _amount(v, field):
    if v is None or v == "":
        return None
    if isinstance(v, float):
        v = repr(v)
    try:
        d = q(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        raise ProposalError(f"{field} must be a money amount")
    return d


def _date(v, field):
    try:
        return date.fromisoformat(str(v))
    except Exception:
        raise ProposalError(f"{field} must be an ISO date (YYYY-MM-DD)")


def validate_payload(db, kind: str, p: dict) -> dict:
    if kind not in KINDS:
        raise ProposalError(f"Unknown proposal kind {kind}")
    if not isinstance(p, dict):
        raise ProposalError("payload must be an object")
    out: dict = {}
    if kind in ("RATE_CHANGE", "PO_REQUIREMENT", "PRECEDENCE_RULE"):
        code = str(p.get("client_code", "")).strip().upper()
        if kind == "PRECEDENCE_RULE" and code == "*":
            out["client_code"] = "*"
        else:
            c = db.scalar(select(Client).where(Client.code == code))
            if not c:
                raise ProposalError(f"Unknown client '{code}'")
            out["client_code"] = code
    if kind == "RATE_CHANGE":
        jt = str(p.get("job_type", "")).strip().upper()
        if not jt:
            raise ProposalError("job_type is required")
        out["job_type"] = jt
        out["client_amount"] = _amount(p.get("client_amount"), "client_amount")
        if jt == "*" and out["client_amount"] is not None:
            raise ProposalError("A base rate needs a specific job type; 'all job types' is only for adjustments")
        out["agent_amount"] = _amount(p.get("agent_amount"), "agent_amount")
        if out["client_amount"] is None and not p.get("modifiers") and p.get("po_required") is None:
            raise ProposalError("Nothing to change: give a client_amount, modifiers or po_required")
        out["effective_from"] = _date(p.get("effective_from"), "effective_from")
        out["close_previous"] = bool(p.get("close_previous", True))
        mods = []
        for m in p.get("modifiers") or []:
            ca = _amount(m.get("client_amount"), "modifier client_amount")
            if ca is None:
                raise ProposalError("Every adjustment needs a client amount (use 0 if only the agent side changes)")
            item = {"client_amount": ca, "agent_amount": _amount(m.get("agent_amount"), "modifier agent_amount")}
            if m.get("factor") or factors.is_factor(str(m.get("condition") or "")):
                key, choice = (str(m["factor"]).strip(), m.get("choice")) if m.get("factor") else factors.split(m["condition"])
                fld = db.scalar(select(PriceField).where(PriceField.key == key, PriceField.status == "ACTIVE"))
                if not fld:
                    raise ProposalError(f"Unknown or retired price factor '{key}'")
                if fld.kind == "CHOICE":
                    match = next((c for c in fld.choices if c.lower() == str(choice or "").strip().lower()), None)
                    if not match:
                        raise ProposalError(f"{fld.label}: choose one of {', '.join(fld.choices)}")
                    choice = match
                elif choice not in (None, ""):
                    raise ProposalError(f"{fld.label} is not a pick-list factor")
                else:
                    choice = None
                inc = m.get("included_units")
                if fld.kind == "NUMBER":
                    try:
                        inc = int(inc or 0)
                    except (TypeError, ValueError):
                        raise ProposalError("included units must be a whole number")
                    if inc < 0:
                        raise ProposalError("included units cannot be negative")
                elif inc not in (None, "", 0):
                    raise ProposalError("included units only apply to number factors")
                else:
                    inc = None
                item.update(condition=factors.condition_for(fld.key, choice), group=None, included_units=inc)
            else:
                cond = str(m.get("condition", "")).upper()
                if cond not in CONDITIONS:
                    raise ProposalError(f"modifier condition must be one of {CONDITIONS} or a price factor")
                item.update(condition=cond, group=m.get("group") or "UPLIFT", included_units=None)
            mods.append(item)
        out["modifiers"] = mods
        out["po_required"] = p.get("po_required") if isinstance(p.get("po_required"), bool) else None
    elif kind == "PRECEDENCE_RULE":
        rule = str(p.get("rule", "")).upper().replace(" ", "")
        hi, sep, lo = rule.partition(">")
        if not sep or hi not in CONDITIONS or lo not in CONDITIONS or hi == lo:
            raise ProposalError("rule must look like EMERGENCY>WEEKEND")
        out["rule"] = rule
        out["effective_from"] = _date(p.get("effective_from"), "effective_from")
    elif kind == "ROLLBACK":
        rc_ = db.get(RateCard, int(p.get("rate_card_id", 0) or 0))
        if not rc_:
            raise ProposalError("Unknown rate card")
        out["rate_card_id"] = rc_.id
        out["target_version"] = int(p.get("target_version", 0))
        if not any(v.version == out["target_version"] for v in rc_.versions):
            raise ProposalError("target_version does not exist")
    elif kind == "PO_REQUIREMENT":
        if not isinstance(p.get("po_required"), bool):
            raise ProposalError("po_required must be true or false")
        out["po_required"] = p["po_required"]
    return out


def _ser(p: dict) -> dict:
    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v
    return conv(p)


# ------------------------------------------------------------------ planning (pure-ish: reads DB, writes nothing)
def _rule_dict(r: PricingRule) -> dict:
    return {"code": r.rule_code, "kind": r.kind, "job_type": r.job_type, "condition": r.condition, "group": r.group,
            "client_amount": str(r.client_amount) if r.client_amount is not None else None,
            "agent_amount": str(r.agent_amount) if r.agent_amount is not None else None, "clause_ref": r.clause_ref,
            "agent_code": r.agent_code, "included_minutes": r.included_minutes,
            "agent_extra_per_30": str(r.agent_extra_per_30) if r.agent_extra_per_30 is not None else None,
            "included_units": r.included_units}


def _next_code(rules: list[dict], db) -> str:
    nums = [int(r["code"][1:]) for r in rules if r["code"][1:].isdigit()]
    allnums = [int(c[1:]) for (c,) in db.execute(select(PricingRule.rule_code)) if c[1:].isdigit()]
    return f"R{max(nums + allnums + [0]) + 1}"


def _card_for(db, client_code):
    c = db.scalar(select(Client).where(Client.code == client_code))
    return c, db.scalar(select(RateCard).where(RateCard.client_id == c.id))


def build_plan(db, kind: str, p: dict) -> dict:
    ops, client_updates = [], []
    if kind == "PO_REQUIREMENT" or (kind == "RATE_CHANGE" and p.get("po_required") is not None):
        c = db.scalar(select(Client).where(Client.code == p["client_code"]))
        client_updates.append({"client_id": c.id, "po_required": p["po_required"]})
    if kind == "RATE_CHANGE" and (p["client_amount"] is not None or p["modifiers"]):
        _, card = _card_for(db, p["client_code"])
        if not card:
            raise ProposalError("Client has no rate card")

        def mutate(rules: list[dict]) -> list[dict]:
            rules = copy.deepcopy(rules)
            if p["client_amount"] is not None:
                bases = [r for r in rules if r["kind"] == "BASE" and r["job_type"] == p["job_type"]]
                if bases:
                    for b in bases:
                        b["client_amount"] = str(p["client_amount"])
                        if p["agent_amount"] is not None:
                            b["agent_amount"] = str(p["agent_amount"])
                else:
                    rules.append({"code": _next_code(rules, db), "kind": "BASE", "job_type": p["job_type"], "condition": None,
                                  "group": None, "client_amount": str(p["client_amount"]),
                                  "agent_amount": str(p["agent_amount"]) if p["agent_amount"] is not None else None,
                                  "clause_ref": "Proposal"})
            for m in p["modifiers"]:
                ex = [r for r in rules if r["kind"] == "MODIFIER" and r["condition"] == m["condition"]
                      and r["job_type"] in (p["job_type"], "*")]
                specific = [r for r in ex if r["job_type"] == p["job_type"]]
                if specific:
                    for r in specific:
                        r["client_amount"] = str(m["client_amount"])
                        r["agent_amount"] = str(m["agent_amount"] if m["agent_amount"] is not None else Decimal("0.00"))
                        r["included_units"] = m.get("included_units")
                else:
                    # A job-type-specific modifier shadows a generic ('*') one with the same condition
                    # (see engine.pricing), and joins the same exclusivity group.
                    rules.append({"code": _next_code(rules, db), "kind": "MODIFIER", "job_type": p["job_type"],
                                  "condition": m["condition"], "group": ex[0]["group"] if ex else m["group"],
                                  "client_amount": str(m["client_amount"]),
                                  "agent_amount": str(m["agent_amount"] if m["agent_amount"] is not None else Decimal("0.00")),
                                  "clause_ref": "Proposal", "included_units": m.get("included_units")})
            return rules
        ops += _successor_ops(card, p["effective_from"], mutate, None, p["close_previous"],
                              f"{p['job_type']} change from {p['effective_from']}")
    if kind == "PRECEDENCE_RULE":
        cards = (list(db.scalars(select(RateCard))) if p["client_code"] == "*"
                 else [_card_for(db, p["client_code"])[1]])
        for card in cards:
            if not card:
                continue
            ops += _successor_ops(card, p["effective_from"], None,
                                  lambda pr: sorted(set(pr) | {p["rule"]}), True, f"Precedence {p['rule']}",
                                  skip_if=lambda v: p["rule"] in (v.precedence or []) or not _has_group_conflict(v, p["rule"]))
    if kind == "ROLLBACK":
        card = db.get(RateCard, p["rate_card_id"])
        target = next(v for v in card.versions if v.version == p["target_version"])
        actives = [v for v in card.versions if v.status == "ACTIVE"]
        latest = max(actives, key=lambda v: v.valid_from)
        if latest.version == target.version:
            raise ProposalError("Target version is already the version in force")
        ops.append({"type": "retire", "rcv_id": latest.id})
        ops.append({"type": "new", "rate_card_id": card.id, "valid_from": latest.valid_from.isoformat(),
                    "valid_to": latest.valid_to.isoformat() if latest.valid_to else None,
                    "rules": [_rule_dict(r) for r in target.rules], "precedence": list(target.precedence or []),
                    "note": f"Rollback: restores v{target.version} behaviour in place of v{latest.version}",
                    "rollback_of_version": latest.version})
    return {"ops": ops, "client_updates": client_updates}


def _has_group_conflict(v: RateCardVersion, rule: str) -> bool:
    hi, _, lo = rule.partition(">")
    groups = {}
    for r in v.rules:
        if r.kind == "MODIFIER" and r.group:
            groups.setdefault(r.group, set()).add(r.condition)
    return any(hi in g and lo in g for g in groups.values())


def _successor_ops(card, eff: date, mutate_rules, mutate_prec, close_previous, note, skip_if=None):
    ops = []
    actives = sorted([v for v in card.versions if v.status == "ACTIVE"], key=lambda v: v.valid_from)
    affected = [v for v in actives if v.valid_to is None or v.valid_to >= eff]
    if not close_previous:
        base = affected[-1] if affected else (actives[-1] if actives else None)
        rules = [_rule_dict(r) for r in base.rules] if base else []
        ops.append({"type": "new", "rate_card_id": card.id, "valid_from": eff.isoformat(), "valid_to": None,
                    "rules": mutate_rules(rules) if mutate_rules else rules,
                    "precedence": mutate_prec(base.precedence or []) if (mutate_prec and base) else list(base.precedence or []) if base else [],
                    "note": note + " (previous version NOT closed)"})
        return ops
    for v in affected:
        if skip_if and skip_if(v):
            continue
        rules = [_rule_dict(r) for r in v.rules]
        new = {"type": "new", "rate_card_id": card.id,
               "valid_from": max(v.valid_from, eff).isoformat(),
               "valid_to": v.valid_to.isoformat() if v.valid_to else None,
               "rules": mutate_rules(rules) if mutate_rules else rules,
               "precedence": mutate_prec(list(v.precedence or [])) if mutate_prec else list(v.precedence or []),
               "note": note}
        if v.valid_from < eff:
            ops.append({"type": "close", "rcv_id": v.id, "valid_to": (eff - timedelta(days=1)).isoformat()})
        else:
            ops.append({"type": "retire", "rcv_id": v.id})
        ops.append(new)
    return ops


# ------------------------------------------------------------------ apply plan to a snapshot (simulation)
def _facts_rule(i, r: dict) -> RuleFacts:
    return RuleFacts(id=-i, code=r["code"], kind=r["kind"], job_type=r["job_type"], condition=r["condition"], group=r["group"],
                     client_amount=Decimal(r["client_amount"]) if r["client_amount"] is not None else None,
                     agent_amount=Decimal(r["agent_amount"]) if r["agent_amount"] is not None else None,
                     clause_ref=r.get("clause_ref", ""), agent_code=r.get("agent_code"),
                     included_minutes=r.get("included_minutes"),
                     agent_extra_per_30=Decimal(r["agent_extra_per_30"]) if r.get("agent_extra_per_30") else None,
                     included_units=r.get("included_units"))


def apply_to_snapshot(db, snap, plan) -> tuple:
    s = copy.deepcopy(snap)
    news = []
    by_id = {v.id: v for v in s.rcvs}
    for i, op in enumerate(plan["ops"]):
        if op["type"] == "close":
            by_id[op["rcv_id"]].valid_to = date.fromisoformat(op["valid_to"])
        elif op["type"] == "retire":
            by_id[op["rcv_id"]].status = "RETIRED"
        elif op["type"] == "new":
            card = db.get(RateCard, op["rate_card_id"])
            nv = RCVFacts(id=-(1000 + i), rate_card_id=card.id, rate_card_name=card.name, client_id=card.client_id,
                          version=max(v.version for v in card.versions) + 1 + len([n for n in news if n.rate_card_id == card.id]),
                          valid_from=date.fromisoformat(op["valid_from"]),
                          valid_to=date.fromisoformat(op["valid_to"]) if op["valid_to"] else None, status="ACTIVE",
                          precedence=op["precedence"], rules=[_facts_rule(k, r) for k, r in enumerate(op["rules"])])
            s.rcvs.append(nv)
            news.append(nv)
    for cu in plan["client_updates"]:
        s.clients[cu["client_id"]].po_required = cu["po_required"]
    return s, news


def conflicts_for(db, snap_after, news) -> list[dict]:
    out = []
    cards = {n.rate_card_id for n in news}
    for cid in cards:
        pool = [v for v in snap_after.rcvs if v.rate_card_id == cid]
        for n in [n for n in news if n.rate_card_id == cid]:
            out += detect(pool, n)
    for n in news:  # every price-factor rule must point at a factor that is defined and active
        for rule in n.rules:
            if factors.is_factor(rule.condition):
                key, _ = factors.split(rule.condition)
                if key not in snap_after.fields:
                    out.append({"code": "UNKNOWN_PRICE_FACTOR",
                                "detail": f"{rule.code} uses price factor '{key}', which is not defined or has been retired"})
    return out


def simulate(db, kind: str, payload: dict) -> dict:
    p = validate_payload(db, kind, payload)
    plan = build_plan(db, kind, p)
    before = snapmod.build(db)
    after, news = apply_to_snapshot(db, before, plan)
    conflicts = conflicts_for(db, after, news)
    jobs = list(db.scalars(select(Job).where(Job.shadow == False)))  # noqa
    cur = {d.job_id: d for d in db.scalars(select(JobDecision).where(JobDecision.is_current == True))}  # noqa
    affected = status_changes = r2r = rv2r = r2b = b2r = 0
    diff = Decimal("0.00")
    hist_invoices = set()
    examples = []
    before_hist = copy.deepcopy(before); before_hist.invoiced = {}
    after_hist = copy.deepcopy(after); after_hist.invoiced = {}
    for j in jobs:
        f = snapmod.job_facts(j)
        d0 = cur.get(j.id)
        if d0 and d0.frozen:
            a = price_job(f, after_hist)
            b = price_job(f, before_hist)
            if a.client_net != b.client_net or a.status != b.status:
                hist_invoices.add(before.invoiced.get(j.id))
            continue
        b = price_job(f, before)
        a = price_job(f, after)
        if (a.status, a.client_net, a.agent_net) == (b.status, b.client_net, b.agent_net):
            continue
        affected += 1
        if b.status != a.status:
            status_changes += 1
        if b.status == "READY" and a.status == "NEEDS_REVIEW":
            r2r += 1
        if b.status == "READY" and a.status == "BLOCKED":
            r2b += 1
        if b.status == "BLOCKED" and a.status == "READY":
            b2r += 1
        if b.status == "NEEDS_REVIEW" and a.status == "READY":
            rv2r += 1
        if a.client_net is not None and b.client_net is not None:
            diff += a.client_net - b.client_net
        elif a.status == "READY" and b.status != "READY":
            diff += a.client_net
        # jobs moving out of READY are not billed in this period, so they are not counted as a price difference
        if len(examples) < 12:
            examples.append({"job_id": j.id, "ref": f.ref, "date": str(j.job_date), "before": b.status,
                             "after": a.status, "before_amount": str(b.client_net) if b.client_net is not None else None,
                             "after_amount": str(a.client_net) if a.client_net is not None else None})
    hist_invoices.discard(None)
    return {"payload": _ser(p), "plan": plan, "conflicts": conflicts, "affected_jobs": affected,
            "status_changes": status_changes, "billing_difference": str(q(diff)), "ready_to_review": r2r,
            "review_to_ready": rv2r, "ready_to_blocked": r2b, "blocked_to_ready": b2r, "historical_invoices_affected": len(hist_invoices),
            "historical_invoice_numbers": sorted(hist_invoices)[:20],
            "historical_note": "Issued invoices are never changed by configuration. Differences would need a credit/debit note process.",
            "examples": examples, "new_versions": [{"rate_card": n.rate_card_name, "version": n.version,
                                                    "valid_from": str(n.valid_from), "valid_to": str(n.valid_to) if n.valid_to else None}
                                                   for n in news]}


def _risk(kind, p, sim) -> tuple[str, list]:
    reasons = []
    today = snapmod.today()
    eff = p.get("effective_from")
    if eff and date.fromisoformat(str(eff)) < today:
        reasons.append("Retroactive effective date")
    if kind == "PRECEDENCE_RULE":
        reasons.append("Changes how critical billing rules combine")
    if kind == "ROLLBACK":
        reasons.append("Configuration rollback")
    if sim["affected_jobs"] > 50:
        reasons.append(f"Bulk change affecting {sim['affected_jobs']} jobs")
    if abs(Decimal(sim["billing_difference"])) > Decimal("1000"):
        reasons.append("Billing impact over £1,000")
    return ("HIGH" if reasons else "NORMAL"), reasons


def four_eyes_policy(db) -> str:
    s = db.get(Setting, "four_eyes_policy")
    return (s.value or {}).get("mode", "ALL") if s else "ALL"


# ------------------------------------------------------------------ proposal lifecycle
def create_proposal(db, actor: str, kind: str, payload: dict, title: str = "", source: str = "HUMAN",
                    automation_id: int | None = None) -> ConfigProposal:
    sim = simulate(db, kind, payload)
    risk, reasons = _risk(kind, sim["payload"], sim)
    status = "BLOCKED_CONFLICT" if sim["conflicts"] else "PENDING_APPROVAL"
    prop = ConfigProposal(kind=kind, title=title or _title(kind, sim["payload"]), payload=sim["payload"], source=source,
                          status=status, risk_level=risk, risk_reasons=reasons, approvals_required=1,
                          conflicts=sim["conflicts"], simulation={k: v for k, v in sim.items() if k != "payload"},
                          created_by=actor, automation_opportunity_id=automation_id)
    db.add(prop)
    db.flush()
    log(db, actor, "AI_PROPOSAL_CREATED" if source == "AI" else "CONFIG_PROPOSED", "config_proposal", prop.id,
        new=status, reason=prop.title, ai="Proposal drafted by AI" if source == "AI" else "None",
        details={"risk": risk, "conflicts": len(sim["conflicts"]), "affected_jobs": sim["affected_jobs"]})
    if sim["conflicts"]:
        log(db, "system", "RULE_CONFLICT_DETECTED", "config_proposal", prop.id, reason="; ".join(c["detail"] for c in sim["conflicts"])[:500])
    db.commit()
    return prop


def _title(kind, p):
    if kind == "RATE_CHANGE":
        bits = []
        if p.get("client_amount"):
            bits.append(f"{p['job_type']} to £{p['client_amount']}")
        for m in p.get("modifiers", []):
            bits.append(f"{_cond_label(m['condition'])} +£{m['client_amount']}"
                        + (" per unit" if m.get("included_units") is not None else ""))
        if p.get("po_required") is not None:
            bits.append("PO " + ("required" if p["po_required"] else "not required"))
        scope = "" if p["job_type"] == "*" or p.get("client_amount") else f"{p['job_type']} "
        return f"{p['client_code']}: {scope}" + ", ".join(bits) + f" from {p['effective_from']}"
    if kind == "PRECEDENCE_RULE":
        return f"{p['client_code'] if p['client_code'] != '*' else 'All clients'}: {p['rule'].replace('>', ' supersedes ')} from {p['effective_from']}"
    if kind == "ROLLBACK":
        return f"Roll back rate card {p['rate_card_id']} to v{p['target_version']} behaviour"
    return f"{p['client_code']}: PO {'required' if p['po_required'] else 'not required'}"


def _cond_label(cond: str) -> str:
    if factors.is_factor(cond):
        key, choice = factors.split(cond)
        return key.replace("_", " ") + (f" = {choice}" if choice else "")
    return cond.title()


def decide(db, actor: str, role: str, proposal_id: int, decision: str, reason: str) -> ConfigProposal:
    prop = db.get(ConfigProposal, proposal_id)
    if not prop:
        raise ProposalError("Unknown proposal")
    if not reason or len(reason.strip()) < 4:
        raise ProposalError("A reason is required")
    if prop.status not in ("PENDING_APPROVAL", "BLOCKED_CONFLICT"):
        raise ProposalError(f"Proposal is {prop.status}")
    if decision == "REJECT":
        db.add(ConfigApproval(proposal_id=prop.id, approver=actor, decision="REJECT", reason=reason))
        prop.status = "REJECTED"
        log(db, actor, "AI_PROPOSAL_REJECTED" if prop.source == "AI" else "CONFIG_REJECTED", "config_proposal", prop.id,
            old="PENDING_APPROVAL", new="REJECTED", reason=reason, role=role)
        notify(db, "RULE_REJECTED", f"Configuration rejected: {prop.title}", reason)
        db.commit()
        return prop
    if decision != "APPROVE":
        raise ProposalError("decision must be APPROVE or REJECT")
    if prop.status == "BLOCKED_CONFLICT":
        raise ProposalError("Proposal has rule conflicts and cannot be activated")
    policy = four_eyes_policy(db)
    if actor == prop.created_by and (policy == "ALL" or prop.risk_level == "HIGH"):
        raise ProposalError("Four-eyes control: the person who made a change cannot approve it")
    # re-validate and re-check conflicts at activation time (configuration may have moved on)
    sim = simulate(db, prop.kind, prop.payload)
    if sim["conflicts"]:
        prop.status = "BLOCKED_CONFLICT"
        prop.conflicts = sim["conflicts"]
        db.commit()
        raise ProposalError("Conflicts detected at activation time; proposal blocked")
    db.add(ConfigApproval(proposal_id=prop.id, approver=actor, decision="APPROVE", reason=reason))
    new_ids = activate(db, actor, prop, sim["plan"])
    prop.status = "ACTIVE"
    prop.resulting_rcv_id = new_ids[-1] if new_ids else None
    vers = ", ".join(f"rcv:{i}" for i in new_ids)
    log(db, actor, "AI_PROPOSAL_APPROVED" if prop.source == "AI" else "CONFIG_APPROVED", "config_proposal", prop.id,
        old="PENDING_APPROVAL", new="ACTIVE", reason=reason, role=role, config_version=vers[:40],
        ai="Proposal drafted by AI; approved by human" if prop.source == "AI" else "None",
        details={"maker": prop.created_by, "checker": actor, "new_versions": new_ids})
    if prop.kind == "ROLLBACK":
        log(db, actor, "CONFIG_ROLLBACK", "rate_card", prop.payload["rate_card_id"], new=f"v{prop.payload['target_version']} behaviour",
            reason=reason, role=role)
    notify(db, "RULE_APPROVED", f"Configuration approved: {prop.title}", f"Approved by {actor}; maker {prop.created_by}")
    db.commit()
    processing.process(db, actor=f"system (after {prop.kind.lower()} #{prop.id})")
    return prop


def activate(db, actor, prop, plan) -> list[int]:
    new_ids = []
    for op in plan["ops"]:
        if op["type"] == "close":
            v = db.get(RateCardVersion, op["rcv_id"])
            v.valid_to = date.fromisoformat(op["valid_to"])
        elif op["type"] == "retire":
            db.get(RateCardVersion, op["rcv_id"]).status = "RETIRED"
    db.flush()
    for op in plan["ops"]:
        if op["type"] != "new":
            continue
        card = db.get(RateCard, op["rate_card_id"])
        nextv = max(v.version for v in card.versions) + 1
        nv = RateCardVersion(rate_card_id=card.id, version=nextv, valid_from=date.fromisoformat(op["valid_from"]),
                             valid_to=date.fromisoformat(op["valid_to"]) if op["valid_to"] else None, status="ACTIVE",
                             precedence=op["precedence"], note=op["note"], created_by=prop.created_by, approved_by=actor,
                             proposal_id=prop.id, rollback_of_version=op.get("rollback_of_version"))
        db.add(nv)
        db.flush()
        for r in op["rules"]:
            db.add(PricingRule(rcv_id=nv.id, rule_code=r["code"], kind=r["kind"], job_type=r["job_type"],
                               condition=r["condition"], group=r["group"],
                               client_amount=Decimal(r["client_amount"]) if r["client_amount"] is not None else None,
                               agent_amount=Decimal(r["agent_amount"]) if r["agent_amount"] is not None else None,
                               clause_ref=r.get("clause_ref", ""), agent_code=r.get("agent_code"),
                               included_minutes=r.get("included_minutes"),
                               agent_extra_per_30=Decimal(r["agent_extra_per_30"]) if r.get("agent_extra_per_30") else None,
                               included_units=r.get("included_units")))
        db.flush()
        db.refresh(card)
        new_ids.append(nv.id)
        log(db, actor, "RATE_CARD_VERSION_CREATED", "rate_card_version", nv.id, new=f"{card.name} v{nextv}",
            reason=op["note"], config_version=f"v{nextv}")
    for cu in plan["client_updates"]:
        c = db.get(Client, cu["client_id"])
        old = c.po_required
        c.po_required = cu["po_required"]
        log(db, actor, "CLIENT_PO_REQUIREMENT_CHANGED", "client", c.code, old=old, new=cu["po_required"])
    return new_ids


def health(db) -> list[dict]:
    """Conflict scan of everything currently active."""
    snap = snapmod.build(db)
    return detect(snap.rcvs)

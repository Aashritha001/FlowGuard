"""Admin-defined price factors (fields that can affect price) and the values jobs carry for them.

Defining or editing a factor never changes a price by itself: money only moves when a pricing rule that uses the
factor is proposed, simulated and approved through config_service (four-eyes). Making a factor *required* can hold
jobs for review (missing data is never invented), so required changes re-run Lane A and report the effect.
"""
import re

from sqlalchemy import select

from ..models import PriceField, RateCardVersion, PricingRule, Job, JobDecision, JobType
from ..engine import factors
from . import processing
from .audit import log, notify


class FieldError(ValueError):
    pass


def _row(db, f: PriceField, usage: dict) -> dict:
    return {"id": f.id, "key": f.key, "label": f.label, "kind": f.kind, "choices": f.choices or [], "unit": f.unit,
            "required_for": f.required_for or [], "description": f.description, "status": f.status,
            "created_by": f.created_by, "created_at": str(f.created_at) if f.created_at else None,
            "rules": usage.get(f.key, [])}


def _usage(db) -> dict:
    """Active pricing rules that use each factor, for display and to stop a factor in use being retired."""
    out: dict[str, list] = {}
    q_ = (select(PricingRule, RateCardVersion).join(RateCardVersion, PricingRule.rcv_id == RateCardVersion.id)
          .where(RateCardVersion.status == "ACTIVE"))
    for r, v in db.execute(q_):
        if factors.is_factor(r.condition):
            key, choice = factors.split(r.condition)
            out.setdefault(key, []).append({"rule": r.rule_code, "rate_card": f"{v.rate_card.name} v{v.version}",
                                            "job_type": r.job_type, "choice": choice,
                                            "client_amount": str(r.client_amount), "agent_amount": str(r.agent_amount),
                                            "included_units": r.included_units})
    return out


def list_fields(db) -> list[dict]:
    usage = _usage(db)
    return [_row(db, f, usage) for f in db.scalars(select(PriceField).order_by(PriceField.status, PriceField.label))]


def _clean(db, data: dict, existing: PriceField | None) -> dict:
    out = {}
    label = str(data.get("label", existing.label if existing else "")).strip()
    if not 2 <= len(label) <= 80:
        raise FieldError("Name must be 2 to 80 characters")
    out["label"] = label
    kind = existing.kind if existing else str(data.get("kind", "")).upper()
    if kind not in factors.KINDS:
        raise FieldError("Type must be yes/no, number or pick-list")
    out["kind"] = kind
    if kind == "CHOICE":
        raw = data.get("choices", existing.choices if existing else [])
        if isinstance(raw, str):
            raw = raw.split(",")
        choices = []
        for c in raw:
            c = str(c).strip()
            if c and c.lower() not in {x.lower() for x in choices}:
                if len(c) > 24 or "=" in c:
                    raise FieldError(f"Option '{c}' must be at most 24 characters and cannot contain '='")
                choices.append(c)
        if len(choices) < 2:
            raise FieldError("A pick-list needs at least two options")
        if existing:  # options used by active rules cannot be removed
            used = {u["choice"] for u in _usage(db).get(existing.key, []) if u["choice"]}
            gone = used - set(choices)
            if gone:
                raise FieldError(f"Options in use by active pricing rules cannot be removed: {', '.join(sorted(gone))}")
        out["choices"] = choices
    else:
        out["choices"] = []
    out["unit"] = str(data.get("unit", existing.unit if existing else "") or "").strip()[:24] if kind == "NUMBER" else ""
    req = data.get("required_for", existing.required_for if existing else [])
    if isinstance(req, str):
        req = [x for x in req.split(",") if x.strip()]
    known = {t.code for t in db.scalars(select(JobType))}
    req = sorted({str(x).strip().upper() if str(x).strip() != "*" else "*" for x in req})
    bad = [x for x in req if x != "*" and x not in known]
    if bad:
        raise FieldError(f"Unknown job type(s): {', '.join(bad)}")
    out["required_for"] = ["*"] if "*" in req else req
    out["description"] = str(data.get("description", existing.description if existing else "") or "").strip()[:1000]
    return out


def _reprocess(db, actor):
    res = processing.process(db, actor=actor)
    return {"counts": res["counts"], "changed": res["changed"]}


def create(db, actor: str, data: dict) -> dict:
    key = str(data.get("key") or "").strip().lower()
    if not key:
        key = re.sub(r"[^a-z0-9]+", "_", str(data.get("label", "")).lower()).strip("_")[:24]
    if key and key[0].isdigit():
        key = "f_" + key[:22]
    if not factors.KEY_RE.match(key or ""):
        raise FieldError("Key must start with a letter and use 2 to 24 lowercase letters, digits or underscores")
    if db.scalar(select(PriceField).where(PriceField.key == key)):
        raise FieldError(f"A price factor with key '{key}' already exists")
    clean = _clean(db, data, None)
    f = PriceField(key=key, created_by=actor, status="ACTIVE", **clean)
    db.add(f)
    db.flush()
    log(db, actor, "PRICE_FACTOR_CREATED", "price_field", key, new=clean["kind"], reason=clean["label"],
        details={k: v for k, v in clean.items() if k != "description"})
    notify(db, "CONFIG", f"New price factor: {clean['label']}",
           "Add a pricing rule that uses it through a configuration proposal; it affects no price until one is approved.")
    db.commit()
    effect = _reprocess(db, actor) if clean["required_for"] else None
    return {"field": _row(db, f, _usage(db)), "effect": effect}


def update(db, actor: str, key: str, data: dict) -> dict:
    f = db.scalar(select(PriceField).where(PriceField.key == key))
    if not f:
        raise FieldError("Unknown price factor")
    if f.status != "ACTIVE":
        raise FieldError("Retired price factors cannot be edited")
    clean = _clean(db, data, f)
    before = {k: getattr(f, k) for k in clean}
    changed = {k: v for k, v in clean.items() if before[k] != v}
    if not changed:
        return {"field": _row(db, f, _usage(db)), "effect": None}
    for k, v in changed.items():
        setattr(f, k, v)
    log(db, actor, "PRICE_FACTOR_UPDATED", "price_field", key, reason=", ".join(changed),
        details={"before": {k: before[k] for k in changed}, "after": changed})
    db.commit()
    effect = _reprocess(db, actor) if "required_for" in changed or "choices" in changed else None
    return {"field": _row(db, f, _usage(db)), "effect": effect}


def retire(db, actor: str, key: str, reason: str) -> dict:
    f = db.scalar(select(PriceField).where(PriceField.key == key))
    if not f or f.status != "ACTIVE":
        raise FieldError("Unknown or already retired price factor")
    if not reason or len(reason.strip()) < 4:
        raise FieldError("A reason is required")
    used = _usage(db).get(key, [])
    if used:
        raise FieldError(f"In use by active pricing rules ({', '.join(u['rule'] for u in used)}). "
                         "Propose a rate-card change that removes them first.")
    f.status = "RETIRED"
    log(db, actor, "PRICE_FACTOR_RETIRED", "price_field", key, old="ACTIVE", new="RETIRED", reason=reason)
    db.commit()
    return {"field": _row(db, f, {}), "effect": _reprocess(db, actor) if f.required_for else None}


def set_job_values(db, actor: str, role: str, job_id: int, values: dict, reason: str) -> dict:
    """Record price-factor values on a job (audited), then re-run Lane A for it. Invoiced jobs are frozen."""
    j = db.get(Job, job_id)
    if not j or j.shadow:
        raise FieldError("Unknown job")
    dec = db.scalar(select(JobDecision).where(JobDecision.job_id == j.id, JobDecision.is_current == True))  # noqa: E712
    if dec and dec.frozen:
        raise FieldError("This job has been invoiced; its facts are frozen. Corrections need a credit/debit note.")
    if not reason or len(reason.strip()) < 4:
        raise FieldError("A reason is required (e.g. where the value came from)")
    if not isinstance(values, dict) or not values:
        raise FieldError("No values given")
    defs = {f.key: f for f in db.scalars(select(PriceField).where(PriceField.status == "ACTIVE"))}
    attrs = dict(j.attributes or {})
    changes = {}
    for key, raw in values.items():
        f = defs.get(key)
        if not f:
            raise FieldError(f"Unknown price factor '{key}'")
        val, err = factors.parse_value(f.kind, f.choices or [], raw)
        if err:
            raise FieldError(f"{f.label}: {err}")
        old = attrs.get(key)
        if old == val or (old is None and val is None):
            continue
        if val is None:
            attrs.pop(key, None)
        else:
            attrs[key] = val
        changes[key] = {"old": old, "new": val}
    if not changes:
        return {"changed": {}, "status": dec.status if dec else None}
    j.attributes = attrs
    for key, ch in changes.items():
        log(db, actor, "JOB_PRICE_FACTOR_SET", "job", j.id, old=str(ch["old"])[:120] if ch["old"] is not None else "",
            new=str(ch["new"])[:120] if ch["new"] is not None else "(cleared)", reason=f"{defs[key].label}: {reason}"[:500],
            role=role)
    db.commit()
    processing.process(db, actor=actor, job_ids=[j.id])
    now_dec = db.scalar(select(JobDecision).where(JobDecision.job_id == j.id, JobDecision.is_current == True))  # noqa: E712
    return {"changed": changes, "status": now_dec.status if now_dec else None,
            "client_net": str(now_dec.client_net) if now_dec and now_dec.client_net is not None else None}

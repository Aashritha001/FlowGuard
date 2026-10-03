"""Rule-conflict detection run before any configuration can be activated."""
from datetime import date
from decimal import Decimal

from .types import RCVFacts

MAX_SANE_AMOUNT = Decimal("5000.00")


def detect(rcvs: list[RCVFacts], candidate: RCVFacts | None = None) -> list[dict]:
    """Returns a list of conflict dicts. Empty list means safe to activate."""
    out: list[dict] = []
    pool = [v for v in rcvs if v.status == "ACTIVE"]
    targets = [candidate] if candidate else pool
    for v in targets:
        if v.valid_to and v.valid_to < v.valid_from:
            out.append({"code": "INVALID_DATE_RANGE", "detail": f"{v.rate_card_name} v{v.version} ends before it starts"})
        for other in pool:
            if other is v or other.id == v.id or other.rate_card_id != v.rate_card_id:
                continue
            if _overlap(v.valid_from, v.valid_to, other.valid_from, other.valid_to):
                out.append({"code": "OVERLAPPING_VERSIONS",
                            "detail": f"{v.rate_card_name} v{v.version} ({v.valid_from} to {v.valid_to or 'open'}) overlaps v{other.version} ({other.valid_from} to {other.valid_to or 'open'})"})
        seen: dict[tuple, str] = {}
        for rule in v.rules:
            if rule.kind == "BASE":
                k = (rule.job_type,)
                if k in seen:
                    out.append({"code": "DUPLICATE_RULE", "detail": f"{rule.code} and {seen[k]} are both base rates for {rule.job_type}"})
                seen[k] = rule.code
            for amt, side in ((rule.client_amount, "client"), (rule.agent_amount, "agent")):
                if amt is None:
                    out.append({"code": "MISSING_REQUIRED_INFORMATION", "detail": f"{rule.code} has no {side} amount"})
                elif amt < 0 or amt > MAX_SANE_AMOUNT:
                    out.append({"code": "IMPOSSIBLE_VALUE", "detail": f"{rule.code} {side} amount £{amt} is outside 0 to £{MAX_SANE_AMOUNT}"})
            if rule.kind == "BASE" and rule.client_amount is not None and rule.agent_amount is not None \
                    and rule.agent_amount > rule.client_amount:
                out.append({"code": "NEGATIVE_MARGIN", "detail": f"{rule.code} pays the agent more than the client is charged"})
            if rule.kind == "MODIFIER" and not rule.condition:
                out.append({"code": "CONTRADICTORY_CONDITION", "detail": f"Modifier {rule.code} has no condition"})
        # contradictory precedence: A>B and B>A
        prs = set(v.precedence or [])
        for p in prs:
            a, _, b = p.partition(">")
            if f"{b}>{a}" in prs:
                out.append({"code": "CONTRADICTORY_CONDITION", "detail": f"Precedence {p} contradicts {b}>{a}"})
    # dedupe
    uniq, keys = [], set()
    for c in out:
        k = (c["code"], c["detail"])
        if k not in keys:
            keys.add(k)
            uniq.append(c)
    return uniq


def _overlap(a1: date, a2: date | None, b1: date, b2: date | None) -> bool:
    a2 = a2 or date.max
    b2 = b2 or date.max
    return a1 <= b2 and b1 <= a2

"""Turns natural language into a STRUCTURED intent from a fixed allow-list. Never produces SQL.
The deterministic parser below works with no LLM; the LLM (if available) may produce the same structure,
which is then validated against the same schema."""
import calendar
import re
from datetime import date, timedelta

INTENTS = {
    "search_jobs", "explain_job", "get_job_trace", "search_invoices", "explain_invoice", "get_money_map",
    "analyse_exceptions", "explain_dashboard", "get_configuration", "create_config_proposal",
    "simulate_config_change", "find_automation_opportunities", "help", "prohibited",
}
STATUS_WORDS = {"blocked": "BLOCKED", "held": "BLOCKED", "review": "NEEDS_REVIEW", "under review": "NEEDS_REVIEW",
                "needs review": "NEEDS_REVIEW", "ready": "READY", "invoice-ready": "READY"}
REASON_WORDS = [("missing po", "MISSING_PO"), ("purchase order", "MISSING_PO"), ("no po", "MISSING_PO"),
                ("possible duplicate", "POSSIBLE_DUPLICATE"), ("duplicate", "POSSIBLE_DUPLICATE"),
                ("vat", "VAT_STATUS_UNCLEAR"), ("self-billing", "SELF_BILLING_AGREEMENT_MISSING"),
                ("self billing", "SELF_BILLING_AGREEMENT_MISSING"), ("missing rate", "RATE_NOT_FOUND"),
                ("no rate", "RATE_NOT_FOUND"), ("multiple rate", "MULTIPLE_RATE_MATCH"), ("two rate", "MULTIPLE_RATE_MATCH"),
                ("modifier", "MODIFIER_CONFLICT"), ("unverified", "UNVERIFIED_JOB"), ("rejected", "REJECTED_JOB"),
                ("conflict", "RULE_CONFLICT")]
GENERIC_WORDS = {"client", "ltd", "limited", "llp", "plc", "group", "the", "and", "co", "company", "services"}


def _job_type(t: str, job_types: list) -> str | None:
    """Matches a configured job type by its code or its full name (longest match wins)."""
    best = None
    for code, name in job_types or []:
        for cand in (code.lower(), (name or "").lower()):
            if cand and re.search(rf"(?<![\w.]){re.escape(cand)}(?![\w.])", t) and (not best or len(cand) > best[1]):
                best = (code, len(cand))
    return best[0] if best else None

# Requests that try to change money, status or controls. The assistant has no tool for any of these.
PROHIBITED = [
    r"\bignore\b.*\b(controls?|instructions?|rules?)\b", r"\bmark\b.*\b(ready|approved|paid)\b",
    r"\b(set|change|make|move)\b.*\b(job|jobs|status)\b.*\bready\b", r"\bapprove\b.*\b(job|invoice|exception|proposal|all)\b",
    r"\b(disable|turn off|bypass|skip|override)\b.*\b(control|check|safety|four.?eyes|rule|verification)\b",
    r"\b(change|edit|alter|increase|reduce)\b.*\binvoice\b.*\b(total|amount|value)\b", r"\bdelete\b",
    r"\bactivate\b", r"\b(drop|truncate)\b.*\btable\b", r"\bselect\b.+\bfrom\b", r"\bgenerate\b.*\binvoices?\b",
]


def _date_range(t: str, today: date):
    month_names = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    if "this month" in t:
        return today.replace(day=1).isoformat(), today.isoformat(), "this month"
    if "last month" in t:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat(), "last month"
    if "this week" in t:
        s = today - timedelta(days=today.weekday())
        return s.isoformat(), today.isoformat(), "this week"
    if "last week" in t:
        s = today - timedelta(days=today.weekday() + 7)
        return s.isoformat(), (s + timedelta(days=6)).isoformat(), "last week"
    if "today" in t:
        return today.isoformat(), today.isoformat(), "today"
    for name, i in month_names.items():
        if re.search(rf"\b(in|during|for)\s+{name}\b", t):
            y = today.year if i <= today.month else today.year - 1
            last = calendar.monthrange(y, i)[1]
            return date(y, i, 1).isoformat(), date(y, i, last).isoformat(), name.title()
    return None


def parse_date_phrase(t: str, today: date):
    """'15 January', '15th Jan 2026', '2026-01-15', '15/01/2026' -> ISO date."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    months.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(months, key=len, reverse=True)) + r")\b\.?(?:\s+(\d{4}))?", t)
    if m:
        d, mo = int(m.group(1)), months[m.group(2)]
        y = int(m.group(3)) if m.group(3) else today.year
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def _client(t: str, clients: list[dict]):
    """By code, full name, 'client X' when the name starts that way, or a distinctive word of the name."""
    for c in clients:
        name = c["name"].lower()
        if c["code"].lower() in re.findall(r"[a-z0-9-]+", t) or name in t:
            return c["code"]
        m = re.match(r"client\s+([a-z0-9])\b", name)
        if m and re.search(rf"\bclient\s+{m.group(1)}\b", t):
            return c["code"]
    for c in clients:
        words = [w for w in re.findall(r"[a-z0-9]+", c["name"].lower()) if w not in GENERIC_WORDS and len(w) > 2]
        if words and re.search(rf"\b{re.escape(words[0])}\b", t):
            return c["code"]
    return None


def _job_id(t: str):
    m = re.search(r"\bfg-?0*(\d+)\b", t) or re.search(r"\bjob\s*#?\s*0*(\d+)\b", t) or re.search(r"#0*(\d+)\b", t)
    return int(m.group(1)) if m else None


def _invoice_no(t: str):
    m = re.search(r"\b(inv|sb|pi)-?0*(\d+)\b", t)
    return f"{m.group(1).upper()}-{int(m.group(2)):05d}" if m else None


def _money(t: str):
    m = re.findall(r"£\s?(\d+(?:\.\d{1,2})?)", t)
    return m


def parse(message: str, today: date, clients: list[dict], role: str, context: dict | None = None,
          job_types: list | None = None) -> dict:
    t = " ".join(message.lower().split())
    context = context or {}
    for pat in PROHIBITED:
        if re.search(pat, t):
            return {"intent": "prohibited", "args": {"matched": pat}}
    job = _job_id(t)
    inv = _invoice_no(t)
    client = _client(t, clients)
    filters: dict = {}
    if client:
        filters["client"] = client
    for w, s in sorted(STATUS_WORDS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(w)}\b", t):
            filters["status"] = s
            break
    for w, code in REASON_WORDS:
        if w in t:
            filters["reason"] = code
            break
    jt = _job_type(t, job_types)
    if jt:
        filters["job_type"] = jt
    dr = _date_range(t, today)
    if dr:
        filters["date_from"], filters["date_to"], filters["date_label"] = dr
    if "weekend" in t and not re.search(r"(get|\+|extra|uplift)", t):
        filters["weekend"] = True
    if "emergency" in t and not re.search(r"(get|\+|extra|uplift|supersede)", t):
        filters["emergency"] = True

    if re.search(r"\b(propose|change|make|set|update|increase|raise|lower)\b", t) and (_money(t) or "po" in t.split() or "mandatory" in t):
        return {"intent": "create_config_proposal", "args": _proposal_args(t, today, client, job_types)}
    if re.search(r"what would happen|simulate|impact of|if we made", t):
        return {"intent": "simulate_config_change", "args": {"proposal_id": context.get("last_proposal_id")}}
    if inv:
        return {"intent": "explain_invoice", "args": {"invoice_number": inv}}
    if job and re.search(r"\btrace\b|lineage", t):
        return {"intent": "get_job_trace", "args": {"job_id": job}}
    if job:
        return {"intent": "explain_job", "args": {"job_id": job}}
    if re.search(r"automation|repeated decisions|learn", t):
        return {"intent": "find_automation_opportunities", "args": {}}
    if re.search(r"money map|where is the money|where.*money.*(held|flow)", t):
        return {"intent": "get_money_map", "args": {}}
    if re.search(r"(money at risk|leak|biggest sources|recurring|top exception|exception causes|summari[sz]e exceptions)", t):
        return {"intent": "analyse_exceptions", "args": {"filters": filters}}
    if re.search(r"why did .*(increase|decrease|go up|go down|change)|dashboard", t):
        return {"intent": "explain_dashboard", "args": {}}
    if re.search(r"configuration|config|rate card|rates? (for|are)|active for", t):
        return {"intent": "get_configuration", "args": {"client": client}}
    if re.search(r"\binvoices?\b", t):
        return {"intent": "search_invoices", "args": {"filters": filters}}
    if filters or re.search(r"\b(show|list|find|which|search)\b", t):
        return {"intent": "search_jobs", "args": {"filters": filters}}
    return {"intent": "help", "args": {}}


def _proposal_args(t, today, client, job_types=None):
    jt = _job_type(t, job_types)
    amounts = _money(t)
    eff = None
    m = re.search(r"\bfrom\s+(.+?)(?:[.,;]|$| weekend| emergency| and | po)", t)
    if m:
        eff = parse_date_phrase(m.group(1), today)
    eff = eff or parse_date_phrase(t, today) or today.isoformat()
    mods = []
    mm = re.search(r"(weekend|emergency|revisit)[a-z ]*?(?:get|gets|\+|extra|uplift of|plus)\s*£\s?(\d+(?:\.\d{1,2})?)", t) \
        or re.search(r"£\s?(\d+(?:\.\d{1,2})?)\s*(?:extra|uplift)?\s*(?:for|on)\s*(weekend|emergency|revisit)", t)
    if mm:
        g = mm.groups()
        cond, amt = (g[0], g[1]) if g[0] in ("weekend", "emergency", "revisit") else (g[1], g[0])
        mods.append({"condition": cond.upper(), "client_amount": amt, "agent_amount": None})
    base_amt = next((a for a in amounts if not mods or a != mods[0]["client_amount"]), None)
    po = True if re.search(r"po (is )?(mandatory|required)|require[sd]? (a )?po|purchase order (is )?(mandatory|required)", t) else None
    if re.search(r"po (is )?(not required|optional)", t):
        po = False
    agent_amt = None
    ma = re.search(r"agent\s+(?:rate|pay|amount)?\s*(?:to|of|at)?\s*£\s?(\d+(?:\.\d{1,2})?)", t)
    if ma:
        agent_amt = ma.group(1)
        if base_amt == agent_amt:
            others = [a for a in amounts if a != agent_amt and (not mods or a != mods[0]["client_amount"])]
            base_amt = others[0] if others else None
    return {"client_code": client, "job_type": jt, "client_amount": base_amt, "agent_amount": agent_amt,
            "effective_from": eff, "modifiers": mods, "po_required": po}


def validate_llm_intent(obj) -> dict | None:
    """Schema check for model output. Anything outside the allow-list is discarded."""
    if not isinstance(obj, dict):
        return None
    it = obj.get("intent")
    args = obj.get("args", obj.get("filters", {}))
    if it not in INTENTS or not isinstance(args, dict):
        return None
    clean = {}
    for k, v in args.items():
        if k in ("job_id", "proposal_id") and isinstance(v, (int, str)) and str(v).isdigit():
            clean[k] = int(v)
        elif k in ("invoice_number", "client", "client_code", "job_type", "effective_from", "client_amount", "agent_amount") \
                and isinstance(v, (str, int)) and len(str(v)) < 40:
            clean[k] = str(v)
        elif k == "filters" and isinstance(v, dict):
            clean[k] = {kk: vv for kk, vv in v.items() if kk in ("client", "status", "reason", "job_type", "date_from",
                                                               "date_to", "weekend", "emergency", "agent")
                        and isinstance(vv, (str, bool)) and len(str(vv)) < 40}
        elif k == "modifiers" and isinstance(v, list):
            clean[k] = [m for m in v if isinstance(m, dict)][:3]
        elif k == "po_required" and isinstance(v, bool):
            clean[k] = v
    return {"intent": it, "args": clean}

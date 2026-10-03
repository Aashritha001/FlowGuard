"""Admin-defined price factors: parsing values and evaluating rule conditions. Pure functions, no I/O.

A modifier rule's condition can refer to a price factor:
    F:<key>            BOOLEAN: applies when the job's value is yes
                       NUMBER:  applies per unit above the rule's included_units (amount x units)
    F:<key>=<choice>   CHOICE:  applies when the job's value is that choice
"""
import re
from decimal import Decimal, InvalidOperation

PREFIX = "F:"
KINDS = ("BOOLEAN", "NUMBER", "CHOICE")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
MAX_UNITS = Decimal("100000")
TRUE = {"1", "true", "yes", "y"}
FALSE = {"0", "false", "no", "n"}


def is_factor(condition: str | None) -> bool:
    return bool(condition) and condition.startswith(PREFIX)


def split(condition: str) -> tuple[str, str | None]:
    body = condition[len(PREFIX):]
    key, sep, choice = body.partition("=")
    return key, (choice if sep else None)


def condition_for(key: str, choice: str | None = None) -> str:
    return f"{PREFIX}{key}" + (f"={choice}" if choice is not None else "")


def parse_value(kind: str, choices: list, raw):
    """Returns (value, error). Empty input is (None, None): 'not provided', never a guessed default.
    Numbers are kept as strings so no float ever enters the money path."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None, None
    if kind == "BOOLEAN":
        if isinstance(raw, bool):
            return raw, None
        v = str(raw).strip().lower()
        if v in TRUE:
            return True, None
        if v in FALSE:
            return False, None
        return None, "must be yes or no"
    if kind == "NUMBER":
        if isinstance(raw, float):
            raw = repr(raw)
        try:
            d = Decimal(str(raw).strip().replace(",", ""))
        except InvalidOperation:
            return None, "must be a number"
        if not d.is_finite() or d < 0 or d > MAX_UNITS:
            return None, f"must be between 0 and {MAX_UNITS}"
        return str(d.normalize() if d == d.to_integral() else d), None
    if kind == "CHOICE":
        v = str(raw).strip()
        match = next((c for c in choices if c.lower() == v.lower()), None)
        return (match, None) if match else (None, f"must be one of {', '.join(choices)}")
    return None, "unknown field type"


def evaluate(condition: str, rule_included_units, fields: dict, attributes: dict | None):
    """Returns (applies, units, problem). units is the multiplier for NUMBER factors, else 1.
    problem is set when the rule refers to a factor that is not (or no longer) defined."""
    key, choice = split(condition)
    f = fields.get(key)
    if not f:
        return False, None, f"price factor '{key}' is not defined"
    raw = (attributes or {}).get(key)
    val, err = parse_value(f.kind, f.choices, raw)
    if err or val is None:
        return False, None, None  # missing / invalid values are caught by the required-data check
    if f.kind == "BOOLEAN":
        return bool(val), Decimal(1), None
    if f.kind == "CHOICE":
        return val == choice, Decimal(1), None
    units = Decimal(val) - Decimal(rule_included_units or 0)
    return units > 0, (units if units > 0 else None), None


def missing_or_invalid(fields: dict, job_type: str | None, attributes: dict | None) -> list[str]:
    """Labels of required factors that are missing for this job type, plus any factor holding an invalid value."""
    out = []
    for f in fields.values():
        raw = (attributes or {}).get(f.key)
        val, err = parse_value(f.kind, f.choices, raw)
        if err:
            out.append(f"{f.label} (invalid value: {err})")
        elif val is None and ("*" in f.required_for or (job_type and job_type in f.required_for)):
            out.append(f.label)
    return out

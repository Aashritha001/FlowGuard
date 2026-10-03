"""Append-only audit trail. Secrets, passwords, tokens and bank details are never passed in."""
from ..models import AuditEvent, Notification

FORBIDDEN_KEYS = {"password", "token", "secret", "csrf", "api_key", "bank_account", "sort_code", "credentials"}


def _scrub(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        if any(f in k.lower() for f in FORBIDDEN_KEYS):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _scrub(v)
        else:
            out[k] = v
    return out


def log(db, actor: str, action: str, entity: str = "", entity_id="", old="", new="", reason="",
        role: str = "", config_version: str = "", ai: str = "None", details: dict | None = None, ts=None) -> AuditEvent:
    ev = AuditEvent(actor=actor, role=role, action=action, entity=entity, entity_id=str(entity_id),
                    old_value=str(old)[:120], new_value=str(new)[:120], reason=reason, config_version=config_version,
                    ai_involvement=ai, details=_scrub(details or {}))
    if ts:
        ev.ts = ts
    db.add(ev)
    return ev


def notify(db, kind: str, title: str, body: str = "", audience: str = "STAFF") -> None:
    db.add(Notification(kind=kind, title=title, body=body, audience=audience))

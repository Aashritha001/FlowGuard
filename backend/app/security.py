"""Authentication, sessions, CSRF and the server-side RBAC permission matrix."""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

from sqlalchemy import select, delete

from .config import settings
from .models import User, Session as DbSession

ROLES = ("SUPER_ADMIN", "FINANCE_ADMIN", "REVIEWER", "AUDITOR", "AGENT")
SA, FA, RV, AU, AG = ROLES

# Permission -> roles. This is the single source of truth, enforced on every API call.
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "dashboard.read":        (SA, FA, RV, AU),
    "jobs.read":             (SA, FA, RV, AU),
    "jobs.read_own":         (AG,),
    "jobs.process":          (SA, FA),
    "reviews.read":          (SA, FA, RV, AU),
    "reviews.decide":        (FA, RV),
    "invoices.read":         (SA, FA, RV, AU),
    "invoices.read_own":     (AG,),
    "invoices.generate":     (FA,),
    "export.sage":           (FA, AU),
    "reconciliation.read":   (SA, FA, AU),
    "reconciliation.run":    (FA, AU),
    "parties.read":          (SA, FA, RV, AU),
    "parties.write":         (SA, FA),
    "config.read":           (SA, FA, RV, AU),
    "config.propose":        (SA, FA),
    "config.approve":        (SA, FA),
    "config.approve_high_risk": (SA, FA),
    "settings.business_date": (SA, FA),
    "fields.manage":         (SA, FA),        # define / edit / retire price factors (no price changes by itself)
    "jobs.edit_fields":      (SA, FA, RV),    # record price-factor values on a job (audited, re-priced by Lane A)
    "imports.run":           (SA, FA),
    "imports.read":          (SA, FA, AU),
    "integrations.read":     (SA, FA, AU),
    "integrations.manage":   (SA,),
    "users.manage":          (SA,),
    "audit.read":            (SA, FA, AU),
    "ai.use":                (SA, FA, RV, AU, AG),
    "ai.read_log":           (SA, FA, AU),
    "automation.read":       (SA, FA, RV, AU),
    "automation.act":        (SA, FA),
    "notifications.read":    (SA, FA, RV, AU, AG),
}


def can(role: str, perm: str) -> bool:
    return role in PERMISSIONS.get(perm, ())


def matrix() -> dict:
    return {p: {r: (r in roles) for r in ROLES} for p, roles in PERMISSIONS.items()}


# ---------------------------------------------------------------- passwords (PBKDF2-HMAC-SHA256, salted)
def _pbkdf2(pw: bytes, salt: bytes, it: int) -> bytes:
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha256", pw, salt, it)
    # Pure-Python RFC 8018 fallback (used only where OpenSSL is absent, e.g. the Pyodide browser preview)
    mac = hmac.new(pw, digestmod="sha256")
    def prf(data):
        m = mac.copy(); m.update(data); return m.digest()
    u = prf(salt + b"\x00\x00\x00\x01")
    out = int.from_bytes(u, "big")
    for _ in range(it - 1):
        u = prf(u)
        out ^= int.from_bytes(u, "big")
    return out.to_bytes(32, "big")


def hash_password(pw: str, iterations: int | None = None) -> str:
    it = iterations or settings.pbkdf2_iterations
    salt = secrets.token_bytes(16)
    dk = _pbkdf2(pw.encode(), salt, it)
    return f"pbkdf2_sha256${it}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        calc = _pbkdf2(pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


# ---------------------------------------------------------------- sessions (opaque token; only hash stored)
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, user: User) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.add(DbSession(token_hash=_h(token), user_id=user.id, csrf_token=csrf,
                     expires_at=datetime.utcnow() + timedelta(hours=settings.session_hours)))
    db.commit()
    return token, csrf


def resolve_session(db, token: str | None):
    if not token:
        return None, None
    s = db.get(DbSession, _h(token))
    if not s or s.expires_at < datetime.utcnow():
        return None, None
    u = db.get(User, s.user_id)
    if not u or not u.active:
        return None, None
    return u, s


def destroy_session(db, token: str | None) -> None:
    if token:
        db.execute(delete(DbSession).where(DbSession.token_hash == _h(token)))
        db.commit()


# ---------------------------------------------------------------- login rate limiting (per username + client)
_attempts: dict[str, list[float]] = {}


def rate_limited(key: str) -> bool:
    now = time.time()
    xs = [t for t in _attempts.get(key, []) if now - t < 900]
    _attempts[key] = xs
    return len(xs) >= settings.login_rate_limit


def record_attempt(key: str) -> None:
    _attempts.setdefault(key, []).append(time.time())


SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"),
    "Cache-Control": "no-store",
}

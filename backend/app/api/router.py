"""Framework-independent request router. FastAPI (server) and the in-browser preview both call dispatch(),
so authentication, CSRF and RBAC are enforced identically in both."""
import json
import re
import traceback
from dataclasses import dataclass, field

from ..security import can, resolve_session


class HTTPError(Exception):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code or {400: "BAD_REQUEST", 401: "UNAUTHENTICATED",
                                                                          403: "FORBIDDEN", 404: "NOT_FOUND",
                                                                          409: "CONFLICT", 429: "RATE_LIMITED"}.get(status, "ERROR")


@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: dict | str | None = None
    token: str | None = None
    csrf: str | None = None
    client_ip: str = "local"
    user: object = None
    session: object = None


@dataclass
class Response:
    status: int = 200
    body: object = None
    content_type: str = "application/json"
    set_session: tuple | None = None   # (token, csrf)
    clear_session: bool = False
    filename: str | None = None


_routes: list = []


def route(method: str, pattern: str, perm: str | None = None, auth: bool = True, csrf: bool = True):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        _routes.append((method, rx, perm, auth, csrf, fn))
        return fn
    return deco


ERRORS = {"count": 0, "last": []}


def dispatch(db, req: Request) -> Response:
    try:
        for method, rx, perm, auth, csrf, fn in _routes:
            if method != req.method:
                continue
            m = rx.match(req.path)
            if not m:
                continue
            if auth:
                user, sess = resolve_session(db, req.token)
                if not user:
                    raise HTTPError(401, "Sign in required")
                req.user, req.session = user, sess
                if csrf and req.method in ("POST", "PUT", "PATCH", "DELETE"):
                    if not req.csrf or req.csrf != sess.csrf_token:
                        raise HTTPError(403, "Missing or invalid CSRF token", "CSRF")
                if perm:
                    perms = perm.split("|")
                    if not any(can(user.role, p) for p in perms):
                        from ..services.audit import log
                        log(db, user.username, "ACCESS_DENIED", "api", req.path, role=user.role, details={"perm": perm})
                        db.commit()
                        raise HTTPError(403, f"Your role ({user.role}) does not have permission for this action")
            out = fn(db, req, **m.groupdict())
            return out if isinstance(out, Response) else Response(body=out)
        raise HTTPError(404, "No such endpoint")
    except HTTPError as e:
        db.rollback()
        return Response(status=e.status, body={"error": e.code, "message": e.message})
    except Exception as e:  # never leak internals
        db.rollback()
        ERRORS["count"] += 1
        ERRORS["last"] = (ERRORS["last"] + [{"path": req.path, "error": type(e).__name__}])[-20:]
        traceback.print_exc()
        return Response(status=500, body={"error": "SERVER_ERROR", "message": "Unexpected error; it has been logged."})


def body(req: Request) -> dict:
    if isinstance(req.body, dict):
        return req.body
    if not req.body:
        return {}
    try:
        b = json.loads(req.body)
        if not isinstance(b, dict):
            raise ValueError
        return b
    except ValueError:
        raise HTTPError(400, "Body must be a JSON object")

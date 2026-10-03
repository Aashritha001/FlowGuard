"""FastAPI entrypoint. A thin HTTP layer over api.router.dispatch: cookies, headers, static frontend."""
import json
import os
import pathlib

from fastapi import FastAPI, Request as FRequest
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse, Response as FResponse

from .config import settings
from .db import SessionLocal, Base, engine, install_append_only_guards, ensure_columns
from .api.router import dispatch, Request
from .api import routes  # noqa: F401  (registers routes)
from .security import SECURE_HEADERS

COOKIE = "fg_session"
FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "index.html"

app = FastAPI(title="FlowGuard", docs_url=None if settings.env == "production" else "/api/docs", redoc_url=None)


@app.on_event("startup")
def _startup():
    if settings.env == "production" and not settings.secret_key:
        raise RuntimeError("FLOWGUARD_SECRET_KEY must be set in production")
    Base.metadata.create_all(engine)
    ensure_columns(engine)
    install_append_only_guards(engine)
    from .setup import bootstrap, load_business_date
    with SessionLocal() as db:
        pw = bootstrap(db)
        load_business_date(db)
    if pw:
        import logging
        logging.getLogger("uvicorn.error").warning(
            "First run: created Super Admin 'admin'%s. Change it under Settings > Your account.",
            "" if settings.admin_password else f" with password {pw}")


@app.middleware("http")
async def headers(request: FRequest, call_next):
    resp = await call_next(request)
    for k, v in SECURE_HEADERS.items():
        resp.headers.setdefault(k, v)
    if settings.cookie_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def index():
    return HTMLResponse(FRONTEND.read_text(encoding="utf-8"))


@app.get("/healthz")
def health():
    return {"ok": True}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api(path: str, request: FRequest):
    raw = await request.body()
    if len(raw) > settings.max_upload_bytes * 4 // 3 + 64 * 1024:  # files arrive base64-encoded
        return JSONResponse({"error": "TOO_LARGE", "message": "Request body too large"}, status_code=413)
    req = Request(method=request.method, path="/api/" + path, query=dict(request.query_params),
                  body=raw.decode("utf-8", errors="replace") if raw else None, token=request.cookies.get(COOKIE),
                  csrf=request.headers.get("x-csrf-token"), client_ip=request.client.host if request.client else "?")
    with SessionLocal() as db:
        out = dispatch(db, req)
    if out.content_type == "application/json":
        resp = FResponse(json.dumps(out.body, default=str), status_code=out.status, media_type="application/json")
    elif isinstance(out.body, (bytes, bytearray)):  # PDFs, ZIPs
        resp = FResponse(content=bytes(out.body), status_code=out.status, media_type=out.content_type)
    else:
        resp = PlainTextResponse(out.body, status_code=out.status, media_type=out.content_type)
        if out.filename:
            resp.headers["Content-Disposition"] = f'attachment; filename="{out.filename}"'
    if out.set_session:
        resp.set_cookie(COOKIE, out.set_session[0], httponly=True, secure=settings.cookie_secure, samesite="strict",
                        max_age=settings.session_hours * 3600, path="/")
    if out.clear_session:
        resp.delete_cookie(COOKIE, path="/")
    return resp

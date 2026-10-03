"""CSV import: upload -> quarantine -> mapping -> schema validation -> preview -> shadow mode (or live import).
Uploaded files are untrusted. Nothing invalid is ever silently imported."""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from ..config import settings
from ..models import now as _now
from ..models import ImportBatch, ImportRow, MappingTemplate, Client, Agent, Job, PriceField
from ..engine.factors import parse_value as parse_factor
from ..engine.pricing import price_job
from ..engine.types import JobFacts
from ..engine.pricing import fuzzy_key
from . import snapshot as snapmod
from . import processing
from .audit import log, notify

FIELDS = {  # FlowGuard field -> (required, type)
    "external_job_id": (True, "str"), "client_code": (True, "str"), "agent_code": (True, "str"),
    "job_type": (True, "str"), "job_date": (True, "date"), "verification_status": (True, "verification"),
    "weekend": (False, "bool"), "emergency": (False, "bool"), "revisit": (False, "bool"),
    "po_number": (False, "str"), "postcode": (False, "str"), "occupancy_status": (False, "str"),
    "legacy_status": (False, "legacy_status"), "legacy_amount": (False, "money"), "notes": (False, "str"),
}
SYNONYMS = {
    "external_job_id": ["work_order_id", "job_id", "jobref", "job_ref", "order_id", "external_job_id"],
    "client_code": ["client", "client_code", "customer", "client_ref"],
    "agent_code": ["contractor_ref", "agent", "agent_code", "agent_id", "contractor"],
    "job_type": ["visit_type", "job_type", "type", "service"],
    "job_date": ["visit_date", "job_date", "date", "completed_date"],
    "verification_status": ["approved_status", "verification_status", "verified", "status"],
    "weekend": ["weekend", "is_weekend"], "emergency": ["emergency", "is_emergency", "urgent"],
    "revisit": ["revisit", "is_revisit"], "po_number": ["po", "po_number", "purchase_order"],
    "postcode": ["postcode", "post_code", "zip"], "occupancy_status": ["occupancy", "occupancy_status"],
    "legacy_status": ["legacy_status", "current_process_status", "billing_status"],
    "legacy_amount": ["legacy_amount", "billed_amount", "current_amount"], "notes": ["notes", "comments"],
}
ALLOWED_EXT = (".csv",)
MAX_ROWS = 20000
VER_MAP = {"verified": "VERIFIED", "approved": "VERIFIED", "yes": "VERIFIED", "y": "VERIFIED",
           "unverified": "UNVERIFIED", "pending": "UNVERIFIED", "no": "UNVERIFIED", "rejected": "REJECTED", "failed": "REJECTED"}
LEGACY_MAP = {"ready": "READY", "invoiced": "READY", "billed": "READY", "held": "BLOCKED", "blocked": "BLOCKED",
              "review": "NEEDS_REVIEW", "needs_review": "NEEDS_REVIEW", "query": "NEEDS_REVIEW"}


class ImportError_(ValueError):
    pass


def safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", (name or "upload.csv").split("/")[-1].split("\\")[-1])[:100]
    return base or "upload.csv"


def upload(db, actor: str, filename: str, content: str) -> ImportBatch:
    fn = safe_filename(filename)
    if not fn.lower().endswith(ALLOWED_EXT):
        raise ImportError_("Only .csv files are accepted")
    if len(content.encode("utf-8")) > settings.max_upload_bytes:
        raise ImportError_(f"File is larger than {settings.max_upload_bytes // 1024} KB")
    if "\x00" in content:
        raise ImportError_("File contains binary data")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    headers = [h.strip() for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise ImportError_("No header row found")
    if len(set(headers)) != len(headers):
        raise ImportError_("Duplicate column names in header")
    b = ImportBatch(filename=fn, uploaded_by=actor, headers=headers, status="QUARANTINED")
    db.add(b)
    db.flush()
    n = 0
    for i, row in enumerate(reader, start=2):
        n += 1
        if n > MAX_ROWS:
            raise ImportError_(f"More than {MAX_ROWS} rows")
        raw = {(k or "").strip(): (v or "").strip()[:500] for k, v in row.items() if k is not None}
        db.add(ImportRow(batch_id=b.id, row_no=i, raw=raw))
    b.summary = {"rows": n}
    b.mapping = suggest_mapping(db, headers)
    log(db, actor, "CSV_UPLOADED", "import_batch", b.id, new="QUARANTINED", details={"file": fn, "rows": n})
    db.commit()
    return b


def suggest_mapping(db, headers: list[str]) -> dict:
    low = {h.lower().replace(" ", "_"): h for h in headers}
    for t in db.scalars(select(MappingTemplate)):
        if all(src in headers for src in t.mapping.values() if src):
            return dict(t.mapping, _template=t.name)
    out = {}
    for f, syns in SYNONYMS.items():
        for s in syns:
            if s in low:
                out[f] = low[s]
                break
    for f in _price_fields(db).values():  # admin-defined price factors match on their key or name
        for cand in (f.key, f.label.lower().replace(" ", "_")):
            if cand in low:
                out[f"field:{f.key}"] = low[cand]
                break
    return out


def _parse(kind, v):
    if v == "" or v is None:
        return None, None
    if kind == "str":
        return v, None
    if kind == "date":
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(v, fmt).date().isoformat(), None
            except ValueError:
                pass
        return None, "invalid date"
    if kind == "bool":
        lv = v.lower()
        if lv in ("1", "true", "yes", "y"):
            return True, None
        if lv in ("0", "false", "no", "n"):
            return False, None
        return None, "invalid yes/no"
    if kind == "verification":
        m = VER_MAP.get(v.lower())
        return (m, None) if m else (None, "unknown verification status")
    if kind == "legacy_status":
        m = LEGACY_MAP.get(v.lower())
        return (m, None) if m else (None, "unknown legacy status")
    if kind == "money":
        try:
            return str(Decimal(v.replace("£", "").replace(",", "")).quantize(Decimal("0.01"))), None
        except InvalidOperation:
            return None, "invalid amount"
    return v, None


def _price_fields(db) -> dict:
    return {f.key: f for f in db.scalars(select(PriceField).where(PriceField.status == "ACTIVE"))}


def apply_mapping(db, actor: str, batch_id: int, mapping: dict, save_as: str | None = None) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    pf = _price_fields(db)
    mapping = {k: v for k, v in mapping.items() if (k in FIELDS or (k.startswith("field:") and k[6:] in pf)) and v}
    for f, (req, _) in FIELDS.items():
        if req and f not in mapping:
            raise ImportError_(f"Required field {f} is not mapped")
    for src in mapping.values():
        if src not in b.headers:
            raise ImportError_(f"Column {src} is not in the file")
    clients = {c.code: c for c in db.scalars(select(Client))}
    agents = {a.code: a for a in db.scalars(select(Agent))}
    live_ids = {e for (e,) in db.execute(select(Job.external_job_id).where(Job.shadow == False))}  # noqa
    seen = {}
    counts = {"valid": 0, "invalid": 0, "duplicate_ids": 0, "missing_fields": 0, "unknown_clients": 0,
              "unknown_agents": 0, "invalid_dates": 0, "already_in_flowguard": 0}
    for row in db.scalars(select(ImportRow).where(ImportRow.batch_id == b.id).order_by(ImportRow.row_no)):
        errs, m = [], {}
        for f, (req, kind) in FIELDS.items():
            src = mapping.get(f)
            val, err = _parse(kind, row.raw.get(src, "")) if src else (None, None)
            if err:
                errs.append(f"{f}: {err}")
                if kind == "date":
                    counts["invalid_dates"] += 1
            elif req and val is None:
                errs.append(f"{f}: missing")
                counts["missing_fields"] += 1
            m[f] = val
        attrs = {}
        for target, src in mapping.items():
            if target.startswith("field:"):
                f = pf[target[6:]]
                val, err = parse_factor(f.kind, f.choices or [], row.raw.get(src, ""))
                if err:
                    errs.append(f"{f.label}: {err}")
                elif val is not None:
                    attrs[f.key] = val
        m["attributes"] = attrs
        if m.get("client_code") and m["client_code"] not in clients:
            errs.append(f"client_code: unknown client {m['client_code']}")
            counts["unknown_clients"] += 1
        if m.get("agent_code") and m["agent_code"] not in agents:
            errs.append(f"agent_code: unknown agent {m['agent_code']}")
            counts["unknown_agents"] += 1
        if m.get("job_date") and m["job_date"] > snapmod.today().isoformat():
            errs.append("job_date: in the future")
            counts["invalid_dates"] += 1
        ext = m.get("external_job_id")
        if ext:
            if ext in seen:
                errs.append(f"external_job_id: duplicate of row {seen[ext]}")
                counts["duplicate_ids"] += 1
            else:
                seen[ext] = row.row_no
            if ext in live_ids:
                m["_already_in_flowguard"] = True
                counts["already_in_flowguard"] += 1
        row.mapped, row.errors = m, errs
        row.status = "INVALID" if errs else "VALID"
        counts["invalid" if errs else "valid"] += 1
    b.mapping = mapping
    b.summary = dict(b.summary, **counts)
    b.status = "VALIDATED"
    if save_as:
        name = re.sub(r"[^A-Za-z0-9 _-]", "", save_as)[:60]
        if name and not db.scalar(select(MappingTemplate).where(MappingTemplate.name == name)):
            db.add(MappingTemplate(name=name, mapping=mapping, created_by=actor))
    log(db, actor, "CSV_VALIDATED", "import_batch", b.id, new="VALIDATED", details=counts)
    notify(db, "CSV_IMPORT", f"CSV import validated: {b.filename}",
           f"{counts['valid']} valid, {counts['invalid']} invalid, {counts['duplicate_ids']} duplicate IDs")
    db.commit()
    return b


def _facts(i, m, clients, agents, batch_id) -> JobFacts:
    c = clients[m["client_code"]]
    a = agents[m["agent_code"]]
    return JobFacts(id=-(i + 1), ref=f"SH-{batch_id}-{i + 1}", external_job_id=m["external_job_id"],
                    source_system=f"CSV:{batch_id}", client_id=c.id, agent_id=a.id, job_type=m["job_type"].upper(),
                    job_date=date.fromisoformat(m["job_date"]), verification_status=m["verification_status"],
                    verified_by="csv", weekend=bool(m.get("weekend")), emergency=bool(m.get("emergency")),
                    revisit=bool(m.get("revisit")), po_number=m.get("po_number"), postcode=m.get("postcode"),
                    created_order=i, attributes=m.get("attributes") or {})


def run_shadow(db, actor: str, batch_id: int) -> dict:
    """SHADOW MODE: prices rows in memory only. Never creates invoices, never writes to source systems."""
    b = db.get(ImportBatch, batch_id)
    if b.status not in ("VALIDATED", "SHADOW_COMPLETE"):
        raise ImportError_("Validate the mapping first")
    clients = {c.code: c for c in db.scalars(select(Client))}
    agents = {a.code: a for a in db.scalars(select(Agent))}
    snap = snapmod.build(db)
    snap.exact_index, snap.fuzzy_index, snap.invoiced = {}, {}, {}  # shadow set is compared against itself only
    rows = list(db.scalars(select(ImportRow).where(ImportRow.batch_id == b.id, ImportRow.status == "VALID").order_by(ImportRow.row_no)))
    facts = [_facts(i, r.mapped, clients, agents, b.id) for i, r in enumerate(rows)]
    for f in facts:
        snap.exact_index.setdefault((f.source_system, f.external_job_id), []).append((f.created_order, f.id, f.ref))
        if f.postcode and f.verification_status == "VERIFIED":
            snap.fuzzy_index.setdefault(fuzzy_key(f), []).append((f.created_order, f.id, f.ref, f.external_job_id))
    res = {"compared": 0, "matching_decisions": 0, "differences": 0, "ready": 0, "blocked": 0, "needs_review": 0,
           "potential_wrong_outputs": 0, "financial_difference": Decimal("0.00"), "legacy_total": Decimal("0.00"),
           "flowguard_total": Decimal("0.00"), "exception_codes": {}, "examples": []}
    for r, f in zip(rows, facts):
        d = price_job(f, snap)
        res[d.status.lower()] += 1
        for rr in d.reasons:
            res["exception_codes"][rr.code] = res["exception_codes"].get(rr.code, 0) + 1
        ls, la = r.mapped.get("legacy_status"), r.mapped.get("legacy_amount")
        out = {"status": d.status, "amount": str(d.client_net) if d.client_net is not None else None,
               "codes": [x.code for x in d.reasons]}
        if d.status == "READY":
            res["flowguard_total"] += d.client_net
        if la and ls == "READY":
            res["legacy_total"] += Decimal(la)
        if ls:
            res["compared"] += 1
            same = (ls == d.status) and (ls != "READY" or la is None or d.client_net == Decimal(la))
            if same:
                res["matching_decisions"] += 1
            else:
                res["differences"] += 1
                if ls == "READY" and d.status != "READY":
                    res["potential_wrong_outputs"] += 1  # legacy billed something FlowGuard would have held
                if len(res["examples"]) < 15:
                    res["examples"].append({"row": r.row_no, "external_id": f.external_job_id, "legacy": ls,
                                            "legacy_amount": la, "flowguard": d.status, "flowguard_amount": out["amount"],
                                            "codes": out["codes"]})
            if ls == "READY" and d.status == "READY" and la:
                res["financial_difference"] += d.client_net - Decimal(la)
        r.mapped = dict(r.mapped, _shadow=out)
    total = len(rows)
    res["rows_processed"] = total
    res["straight_through_rate"] = f"{(100 * res['ready'] / total):.1f}" if total else "0.0"
    for k in ("financial_difference", "legacy_total", "flowguard_total"):
        res[k] = str(res[k])
    res["invoices_sent"] = 0
    res["source_writes"] = 0
    b.shadow_result = res
    b.status = "SHADOW_COMPLETE"
    log(db, actor, "SHADOW_RUN", "import_batch", b.id, new="SHADOW_COMPLETE",
        details={k: res[k] for k in ("compared", "differences", "potential_wrong_outputs", "straight_through_rate")})
    notify(db, "CSV_IMPORT", f"Shadow run complete: {b.filename}", f"{res['compared']} compared, {res['differences']} differences")
    db.commit()
    return res


def import_live(db, actor: str, batch_id: int) -> dict:
    """Promote VALID rows to live jobs (source CSV). Lane A then prices them like any other source."""
    b = db.get(ImportBatch, batch_id)
    if b.status not in ("VALIDATED", "SHADOW_COMPLETE"):
        raise ImportError_("Validate the mapping first")
    clients = {c.code: c for c in db.scalars(select(Client))}
    agents = {a.code: a for a in db.scalars(select(Agent))}
    ids = []
    for r in db.scalars(select(ImportRow).where(ImportRow.batch_id == b.id, ImportRow.status == "VALID")):
        m = r.mapped
        j = Job(external_job_id=m["external_job_id"], client_id=clients[m["client_code"]].id,
                agent_id=agents[m["agent_code"]].id, job_type=m["job_type"].upper(), job_date=date.fromisoformat(m["job_date"]),
                verification_status=m["verification_status"],
                verification_timestamp=_now() if m["verification_status"] == "VERIFIED" else None,
                verified_by="csv-import" if m["verification_status"] == "VERIFIED" else None,
                weekend=bool(m.get("weekend")), emergency=bool(m.get("emergency")), revisit=bool(m.get("revisit")),
                po_number=m.get("po_number"), postcode=m.get("postcode"), occupancy_status=m.get("occupancy_status"),
                notes=m.get("notes") or "", source_system="CSV_IMPORT", source_record_id=f"csv:{b.id}:{r.row_no}",
                attributes=m.get("attributes") or None,
                import_batch_id=b.id)
        db.add(j)
        db.flush()
        ids.append(j.id)
    b.status = "IMPORTED_LIVE"
    log(db, actor, "CSV_IMPORTED_LIVE", "import_batch", b.id, new=f"{len(ids)} jobs")
    db.commit()
    res = processing.process(db, actor=actor, job_ids=ids)
    return {"imported": len(ids), "counts": res["counts"]}

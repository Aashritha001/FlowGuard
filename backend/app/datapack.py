"""Loads the GSC Job-to-Cash data pack (Excel) into a FlowGuard database and checks the result.

    python -m app.datapack <workbook.xlsx | workbook.csv>          # -> ./flowguard_datapack.db
    python -m app.datapack <workbook> --db sqlite:///./other.db --report ../docs/DATAPACK_TEST_REPORT.md

The pay-run is worked out from the workbook: the period is the month of the latest visit, client invoices are dated
the last day of that month, and agents are paid on the 15th of the following month for work verified by then.
The report contains the workbook's data: it is git-ignored and must not be published.

What it does
  1. Maps the workbook onto FlowGuard's normalised schema. One FlowGuard job per agent task (that is where
     agent pay lives). The client's fixed job price sits on the task done by the category's lead role; every
     other task in the same job carries a £0 client side, so the client is billed once per job.
  2. Runs the real pipeline: Lane A engine -> invoices for the pay-run -> reconciliation -> reconstruction.
  3. Prices every task again with an independent reference calculator written straight from the
     workbook's Rates_and_Details sheet (it does not call the engine), and compares the two task by task.
     The engine must never be READY where the reference is not, and READY amounts must match to the penny.

Interpretations of the workbook (the pack does not spell these out) are listed in ASSUMPTIONS and in the report.
"""
import argparse
import collections
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import workbook
from .config import settings
from .db import Base, make_engine, install_append_only_guards
from .models import Client, Agent, Setting, Job, JobDecision, Invoice, InvoiceLine
from .services import processing, invoicing, reconciliation, reconstruction
from .services.audit import log, notify

SOURCE = workbook.SOURCE
# Set from the workbook by set_period(): agents are paid on the 15th for tasks verified by the end of the previous month.
PAYRUN: date | None = None
CUTOFF: date | None = None
CLIENT_ISSUE: date | None = None


def set_period(pack) -> None:
    global PAYRUN, CUTOFF, CLIENT_ISSUE
    last = max(dmy(t["Visit Date"]) for t in pack.tasks if t.get("Visit Date"))
    nxt = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    CUTOFF = CLIENT_ISSUE = date.fromordinal(nxt.toordinal() - 1)
    PAYRUN = nxt.replace(day=15)
PENNY = D("0.01")
money, dmy, read_pack, Pack = workbook.money, workbook.dmy, workbook.read_pack, workbook.Pack

ASSUMPTIONS = [
    "One FlowGuard job per agent task. The client's fixed job price is carried by the completed task of the "
    "category's lead role (the role present on most jobs of that category); other roles on the same job are paid "
    "to the agent with a £0 client side, so the client is billed once per job.",
    "Failed Visit and No Show tasks have no rate in the pack, so they are not priced: they go to review.",
    "Pay-run eligibility: only tasks verified by the end of the period's month are invoiced in this run (paid on "
    "the 15th of the next month). Tasks verified later stay READY for the next run.",
    "A self-billing agreement is required only when the payee is VAT registered on the task date (HMRC "
    "self-billing applies to VAT invoices). Payees who send their own invoices get a purchase invoice (PI-) "
    "record instead of a self-bill (SB-).",
    "Sub-agents are paid through their lead agent company: VAT, self-billing and the agent invoice follow "
    "the payee, and a lead company's special rate applies to its sub-agents where the rate sheet says so.",
    "A VAT registration date after the task date means no VAT on that task; an agent invoice is split if a "
    "payee's VAT position changes inside the pay period.",
    "Time-based roles: the rate covers the first 60 minutes, then the extra rate per 30 minutes or part of 30.",
    "Self-billing agreements recorded as 'Yes' have no start date in the pack, so they are treated as long-standing.",
    "Postcodes in the pack are outward codes only (e.g. 'IP1'), so FlowGuard's possible-duplicate signal "
    "(same agent, date, postcode, job type) is coarser than usual.",
]


def load(db, pack: Pack, filename: str = "workbook.xlsx") -> dict:
    """Loads the workbook through the same importer the web app uses (no users, no invented data)."""
    set_period(pack)
    settings.business_date = PAYRUN.isoformat()  # business date = pay-run date for this data set
    s = workbook.import_workbook(db, "datapack-test", pack, filename, reprocess=False)
    return {"tasks": s["tasks_added"], "agents": s["agents_added"], "clients": s["clients_added"]}


def run_pipeline(db) -> dict:
    proc = processing.process(db, actor="system")
    ready = []
    for job, dec in invoicing.eligible(db):
        if job.verification_timestamp and job.verification_timestamp.date() <= CUTOFF:
            ready.append(job.id)
    batch = invoicing.generate_for_jobs(db, "finance", ready, issue_date=CLIENT_ISSUE,
                                        batch_ref=f"BATCH-{PAYRUN:%Y%m%d}-001", agent_issue_date=PAYRUN) if ready else None
    db.commit()
    rec = reconciliation.run(db, "system").result
    repro = collections.Counter()
    for inv in db.scalars(select(Invoice).order_by(Invoice.id)):
        repro[reconstruction.reproduce(db, inv.id)["result"]] += 1
    notify(db, "SYSTEM", "Data pack processed", f"{proc['processed']} tasks; batch {batch.reference if batch else '-'}")
    db.commit()
    return {"processing": proc, "invoiced_tasks": len(ready), "batch": batch.reference if batch else None,
            "reconciliation": rec, "reconstruction": dict(repro)}


# ---------------------------------------------------------------------------------------------- reference
def reference(pack: Pack, cfg: dict) -> dict:
    """Independent re-statement of the pack's rules. Shares no pricing code with the engine; it only reuses the
    importer's category -> lead-role mapping (cfg), which is a data-mapping decision, not a price."""
    out = {}
    for t in pack.tasks:
        gj = pack.jobs.get(t["GSC Job Ref"]) or {}
        category, role = gj.get("Job Category"), t["Role"]
        agent = pack.agents.get(t["Agent ID"]) or {}
        payee_id = agent.get("Paid Via (Agent ID)") or t["Agent ID"]
        payee = pack.agents.get(payee_id) or {}
        visit, vdate = dmy(t["Visit Date"]), dmy(t["Verification Date"])
        r = {"payee": payee_id, "client": gj.get("Client"), "client_net": None, "agent_net": None, "vat_rate": None,
             "payable": False, "why": "", "in_run": False}
        out[t["Agent Job Ref"]] = r
        if t["Verification"] == "Rejected":
            r["why"] = "rejected"
            continue
        if t["Verification"] != "Verified":
            r["why"] = "not verified"
            continue
        if t["Status"] != "Completed":
            r["why"] = f"{t['Status'].lower()} (no rate in pack)"
            continue
        cat = cfg.get(category)
        if not cat or role not in cat["roles"] or role not in pack.std_rates or (gj.get("Client"), category) not in pack.client_prices:
            r["why"] = "no price for this client/category/role"
            continue
        if (t["Agent ID"], role) in pack.special_rates:
            rate, extra, _ = pack.special_rates[(t["Agent ID"], role)]
        elif (payee_id, role) in pack.special_rates:
            rate, extra, _ = pack.special_rates[(payee_id, role)]
        else:
            rate, extra = pack.std_rates[role]
        extra = extra if extra is not None else pack.std_rates[role][1]
        mins = t["Time On Site (mins)"]
        if extra is not None:
            if mins is None:
                r["why"] = "time on site missing"
                continue
            over = max(0, mins - 60)
            rate = rate + extra * ((over + 29) // 30)
        registered = payee.get("VAT Registered") == "Yes" and payee.get("VAT Number")
        reg_from = dmy(payee.get("VAT Registered From"))
        vat = D("0.20") if registered and (reg_from is None or visit >= reg_from) else D("0")
        if vat > 0 and payee.get("Sends Own Invoices") != "Yes" and payee.get("Self-Billing Agreement") != "Yes":
            r["why"] = "VAT-registered payee, no self-billing agreement, does not send own invoices"
            continue
        r.update(payable=True, client_net=money(pack.client_prices[(gj["Client"], category)]) if role == cat["lead"] else D("0.00"),
                 agent_net=money(rate), vat_rate=vat, in_run=bool(vdate and vdate <= CUTOFF), why="payable")
    return out


def categories(db) -> dict:
    return db.get(Setting, "workbook_categories").value


def compare(db, pack: Pack, ref: dict) -> dict:
    rows = db.execute(select(Job, JobDecision).join(JobDecision, JobDecision.job_id == Job.id)
                      .where(JobDecision.is_current == True, Job.source_system == SOURCE)).all()  # noqa: E712
    cats = collections.Counter()
    wrong, held, held_reasons, ref_blocked_reasons = [], [], collections.Counter(), collections.Counter()
    engine_reasons = collections.Counter()
    for job, dec in rows:
        r = ref[job.external_job_id]
        codes = [x.code for x in dec.reasons]
        for c in codes:
            engine_reasons[c] += 1
        if dec.status == "READY":
            if not r["payable"]:
                cats["WRONG"] += 1
                wrong.append({"task": job.external_job_id, "problem": f"engine READY but reference says {r['why']}"})
            elif (money(dec.client_net), money(dec.agent_net)) != (r["client_net"], r["agent_net"]):
                cats["WRONG"] += 1
                wrong.append({"task": job.external_job_id, "problem": "amount differs",
                              "engine": [str(dec.client_net), str(dec.agent_net)],
                              "reference": [str(r["client_net"]), str(r["agent_net"])]})
            else:
                cats["MATCH_READY"] += 1
        elif r["payable"]:
            cats["HELD_BY_ENGINE"] += 1
            held_reasons[",".join(codes)] += 1
            held.append({"task": job.external_job_id, "status": dec.status, "reasons": codes,
                         "message": dec.reasons[0].message if dec.reasons else ""})
        else:
            cats["MATCH_NOT_PAYABLE"] += 1
            ref_blocked_reasons[r["why"]] += 1
    # invoice-level check: what the reference would put on this pay-run's invoices, given what was invoiced
    invoiced = {ln.job_id for ln in db.scalars(select(InvoiceLine))}
    exp_client, exp_agent = collections.Counter(), collections.Counter()
    for job, dec in rows:
        r = ref[job.external_job_id]
        if job.id in invoiced and r["payable"]:
            if r["client_net"]:
                exp_client[r["client"]] += r["client_net"]
            exp_agent[(r["payee"], str(D(r["vat_rate"]).normalize()))] += r["agent_net"]
    gen_client, gen_agent = collections.Counter(), collections.Counter()
    for inv in db.scalars(select(Invoice)):
        if inv.kind == "CLIENT":
            gen_client[db.get(Client, inv.client_id).name] += inv.net
        else:
            gen_agent[(db.get(Agent, inv.agent_id).code, str(D(inv.vat_rate).normalize()))] += inv.net
    inv_diff = [{"party": k, "reference": str(exp_client[k]), "generated": str(gen_client[k])}
                for k in set(exp_client) | set(gen_client) if money(exp_client[k]) != money(gen_client[k])]
    inv_diff += [{"party": f"{k[0]} @ {k[1]}", "reference": str(exp_agent[k]), "generated": str(gen_agent[k])}
                 for k in set(exp_agent) | set(gen_agent) if money(exp_agent[k]) != money(gen_agent[k])]
    ref_in_run_not_invoiced = sum(1 for job, dec in rows if ref[job.external_job_id]["payable"]
                                  and ref[job.external_job_id]["in_run"] and job.id not in invoiced)
    return {"categories": dict(cats), "wrong": wrong, "held": held, "held_reasons": dict(held_reasons),
            "not_payable_reasons": dict(ref_blocked_reasons), "engine_reasons": dict(engine_reasons),
            "invoice_differences": inv_diff, "reference_in_run_but_not_invoiced": ref_in_run_not_invoiced,
            "client_totals": {k: str(money(v)) for k, v in gen_client.items()},
            "agent_invoices": sum(1 for i in db.scalars(select(Invoice)) if i.kind == "AGENT")}


def data_quality(pack: Pack) -> list[str]:
    notes = []
    jobs, tasks = pack.jobs, pack.tasks
    grp = collections.defaultdict(list)
    for j in jobs.values():
        grp[(j["Client"], j["Customer Name"], j["Postcode"], j["Job Category"])].append(j["GSC Job Ref"])
    dup = [v for v in grp.values() if len(v) > 1]
    named = [v for k, v in grp.items() if len(v) > 1 and "occupier" not in str(k[1]).lower()]
    notes.append(f"{len(dup)} groups of GSC jobs share client, customer name, outward postcode and category "
                 f"({len(named)} of them with a named customer rather than 'The Occupier'). Client refs differ, so "
                 "they are treated as separate jobs; worth a human glance before billing.")
    late = sum(1 for t in tasks if t["Verification"] == "Verified" and dmy(t["Verification Date"]) > CUTOFF)
    notes.append(f"{late} tasks were verified after {CUTOFF:%d/%m/%Y} and belong to the next pay-run.")
    mid = [a for a, x in pack.agents.items() if dmy(x["VAT Registered From"]) and CUTOFF.replace(day=1) <= dmy(x["VAT Registered From"]) <= CUTOFF]
    if mid:
        notes.append("VAT registration starts inside January for " + ", ".join(mid) + ": tasks before that date carry no VAT.")
    nosba = [a for a, x in pack.agents.items() if x["VAT Registered"] == "Yes" and x["Self-Billing Agreement"] != "Yes"
             and x["Sends Own Invoices"] != "Yes"]
    if nosba:
        notes.append("VAT-registered payees with no self-billing agreement who do not send their own invoices: "
                     + ", ".join(nosba) + ". GSC cannot lawfully self-bill them VAT until an agreement is signed.")
    own = [a for a, x in pack.agents.items() if x["Sends Own Invoices"] == "Yes"]
    notes.append(f"{len(own)} payees send their own invoices ({', '.join(own)}): FlowGuard records the expected "
                 "purchase invoice (PI-) for matching against theirs rather than issuing a self-bill.")
    return notes


# ---------------------------------------------------------------------------------------------- report
def report_md(pack, loaded, res, cmp_, dq) -> str:
    rec = res["reconciliation"]
    c = cmp_["categories"]
    ok = not cmp_["wrong"] and not cmp_["invoice_differences"] and rec["mismatch_count"] == 0 \
        and set(res["reconstruction"]) <= {"REPRODUCED SUCCESSFULLY"}
    L = [f"# Data pack test: GSC Job-to-Cash, {CUTOFF:%B %Y}", "",
         f"**Result: {'PASS' if ok else 'FAIL'}**: "
         + ("no task was priced or invoiced differently from the independent reference, reconciliation found no "
            "mismatches, and every invoice reproduces from its recorded configuration." if ok else
            "see the differences below."), "",
         "Generated by `python -m app.datapack`. The engine processed every task with no manual intervention; the reference calculator "
         "re-prices every task straight from the Rates_and_Details sheet without calling the engine.", "",
         "## What was loaded", "",
         f"| | Count |\n|---|---|\n| GSC jobs | {len(pack.jobs)} |\n| Agent tasks (FlowGuard jobs) | {loaded['tasks']} |\n"
         f"| Agents | {loaded['agents']} |\n| Clients | {loaded['clients']} |", "",
         "## Lane A decisions", "",
         "| Status | Tasks |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in res["processing"]["counts"].items()), "",
         "Reason codes raised by the engine:", "",
         "| Reason | Tasks |\n|---|---|\n" + "\n".join(f"| `{k}` | {v} |" for k, v in sorted(cmp_["engine_reasons"].items(), key=lambda x: -x[1])), "",
         "## Engine vs independent reference (task by task)", "",
         "| Outcome | Tasks | Meaning |\n|---|---|---|\n"
         f"| Match: READY, same amounts | {c.get('MATCH_READY', 0)} | both sides priced to the penny as the reference |\n"
         f"| Match: not payable | {c.get('MATCH_NOT_PAYABLE', 0)} | both agree it must not be invoiced |\n"
         f"| Held by engine | {c.get('HELD_BY_ENGINE', 0)} | reference would pay; engine sent to a human (safe direction) |\n"
         f"| **Wrong** | **{c.get('WRONG', 0)}** | engine READY where it should not be, or a different amount |", "",
         "Why the reference says not payable:", "",
         "| Reason | Tasks |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in sorted(cmp_["not_payable_reasons"].items(), key=lambda x: -x[1])), ""]
    if cmp_["held_reasons"]:
        L += ["Why the engine held tasks the reference would pay:", "",
              "| Engine reasons | Tasks |\n|---|---|\n" + "\n".join(f"| `{k}` | {v} |" for k, v in cmp_["held_reasons"].items()), ""]
    if cmp_["wrong"]:
        L += ["### Wrong outputs", "", "```", *[str(w) for w in cmp_["wrong"][:50]], "```", ""]
    L += ["## Pay-run invoices (Lane B)", "",
          f"Batch `{res['batch']}`: {res['invoiced_tasks']} tasks verified by {CUTOFF:%d/%m/%Y}. "
          f"Client invoices dated {CLIENT_ISSUE:%d/%m/%Y}, agent invoices {PAYRUN:%d/%m/%Y}; numbering continues "
          f"from INV-{pack.numbering.get('INV')} and SB-{pack.numbering.get('SB')}.", "",
          "| Client | Net invoiced |\n|---|---|\n" + "\n".join(f"| {k} | £{v} |" for k, v in sorted(cmp_["client_totals"].items())), "",
          f"Agent invoices: {cmp_['agent_invoices']}. Invoice totals that differ from the reference: "
          f"**{len(cmp_['invoice_differences'])}**. Tasks the reference would put in this run that were not invoiced: "
          f"{cmp_['reference_in_run_but_not_invoiced']} (all held by the engine, above).", "",
          "## Reconciliation and reconstruction", "",
          f"| Check | Value |\n|---|---|\n| Expected client net | £{rec['expected_client_total']} |\n"
          f"| Generated client net | £{rec['generated_client_total']} |\n| Expected agent net | £{rec['expected_agent_total']} |\n"
          f"| Generated agent net | £{rec['generated_agent_total']} |\n| Invoice lines expected / generated | "
          f"{rec['expected_invoice_lines']} / {rec['generated_invoice_lines']} |\n| Mismatches | {rec['mismatch_count']} |\n"
          f"| READY awaiting next run | {rec['ready_awaiting_invoice']} (£{rec['ready_awaiting_value']} client net) |\n"
          + "\n".join(f"| Reconstruction: {k} | {v} invoices |" for k, v in res["reconstruction"].items()), "",
          "## Data-quality observations", "", *[f"- {n}" for n in dq], "",
          "## Interpretations used (not stated in the pack)", "", *[f"- {a}" for a in ASSUMPTIONS], ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xlsx")
    ap.add_argument("--db", default="sqlite:///./flowguard_datapack.db")
    ap.add_argument("--report", default=str(Path(__file__).resolve().parents[2] / "docs" / "DATAPACK_TEST_REPORT.md"))
    args = ap.parse_args(argv)
    pack = read_pack(args.xlsx)
    eng = make_engine(args.db)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    with S() as db:
        loaded = load(db, pack, Path(args.xlsx).name)
        res = run_pipeline(db)
        cmp_ = compare(db, pack, reference(pack, categories(db)))
    install_append_only_guards(eng)
    dq = data_quality(pack)
    md = report_md(pack, loaded, res, cmp_, dq)
    Path(args.report).write_text(md, encoding="utf-8")
    c = cmp_["categories"]
    print(f"Loaded {loaded['tasks']} tasks into {args.db}")
    print(f"Lane A: {res['processing']['counts']}")
    print(f"vs reference: {c}  invoice differences: {len(cmp_['invoice_differences'])}")
    print(f"Reconciliation mismatches: {res['reconciliation']['mismatch_count']}  reconstruction: {res['reconstruction']}")
    print(f"Report: {args.report}")
    return 0 if not cmp_["wrong"] and not cmp_["invoice_differences"] and res["reconciliation"]["mismatch_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

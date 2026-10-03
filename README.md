# FlowGuard — GSC Job-to-Cash Financial Control Platform

Job-to-Cash financial control for GSC Agency: verified work → contractual price and readiness checks (**Lane A**) → client invoice and agent invoice (**Lane B**) → reconciliation → Sage-ready export, with the Money Map, review and approval screens.

The application ships with **no business data**. You load your own: the GSC workbook (.xlsx), CSV files, or (later) a live integration.

> VAT handling is configuration-driven; confirm the treatment with your tax adviser. See *Known limitations* before production use.

## Design principles (and where they live in the code)

| Principle | Where it is enforced |
|---|---|
| Rules decide the money. AI explains and improves the rules. | `app/engine/pricing.py` is a pure function; `app/ai/gateway.py` has no tool that writes money |
| Nothing financial happens without a trace. | Every `JobDecision` stores a full trace; invoices store config versions; append-only audit table |
| Uncertainty never becomes an invoice — it becomes a review. | No "probably okay" state: only `READY`, `BLOCKED`, `NEEDS_REVIEW` (DB `CHECK` constraint) |
| Local by design. Traceable by default. | Ollama only, via a backend tool gateway; external AI APIs not used |

## The business problem

After a GSC admin has verified a job, someone still has to find the right contractual price, check PO/billing/VAT/self-billing requirements, raise a client invoice and an agent invoice, and prove the two sides reconcile. FlowGuard automates the deterministic part, routes everything uncertain to a human with the evidence, and never produces a wrong financial output silently.

```
Client sends job → Agent completes → GSC admin verifies → FLOWGUARD
  → Lane A: contract, rate-card version, rule, modifiers, PO, VAT, self-billing, duplicates
  → READY / BLOCKED / NEEDS_REVIEW (reason code + evidence + rule + team)
  → READY only → Lane B: client (sales) invoice + agent (self-billed / purchase) invoice
  → Reconciliation → Sage-ready export
```

## Run it

### Python
```bash
pip install -r requirements.txt
cd backend
python -m app.setup            # first run: creates the database and the 'admin' Super Admin, prints its password
uvicorn app.main:app --port 8000
# open http://localhost:8000
```
Set `FLOWGUARD_ADMIN_PASSWORD` before the first run to choose the admin password instead. If you skip `app.setup`, the server does the same on first start and writes the generated password to its log once.

### Docker (with local Ollama)
```bash
cp .env.example .env            # set FLOWGUARD_SECRET_KEY and FLOWGUARD_ADMIN_PASSWORD
docker compose up --build
docker compose exec ollama ollama pull <any-local-model>
```
The database lives in the `flowguard-data` volume. Set `OLLAMA_MODEL` in `.env`, or leave it empty to use the first local model.

### Vercel
The repository is ready to import into Vercel: `index.py` (root) is the entrypoint, `vercel.json` bundles the
frontend, and `.vercelignore` keeps local data out of the upload.

1. **Add New → Project → Import** this GitHub repository. Framework preset: **FastAPI** (or **Other**). Leave the
   root directory as the repository root; no build command is needed (`frontend/index.html` is committed).
2. **Add a Postgres database** so data survives restarts: **Storage → Create → Neon (Postgres)** and connect it to
   the project. It sets `DATABASE_URL`, which FlowGuard picks up automatically (any `postgres://…` URL works).
3. **Environment variables** (Settings → Environment Variables):
   | Name | Value |
   |---|---|
   | `FLOWGUARD_ADMIN_PASSWORD` | the first `admin` password (12+ characters, upper and lower case, a number) |
   | `FLOWGUARD_SECRET_KEY` | a long random string |
   | `FLOWGUARD_ENV` | `production` |
4. **Deploy**, sign in as `admin`, then follow *First steps* below.

Without a database the app still starts, but keeps its data in `/tmp`, which Vercel wipes whenever an instance
stops; the app shows a **"Temporary storage: data resets"** warning in that case. Uploads and downloads are
limited to 4.5 MB each by Vercel (the data-pack CSV is well under that). The local AI assistant (Ollama) is not
reachable from Vercel, so the assistant runs in its built-in structured mode.

### PostgreSQL
Set `DATABASE_URL=postgresql+psycopg://…` and install `psycopg[binary]`. Money columns become `NUMERIC(14,2)`; append-only triggers are created for PostgreSQL too (`app/db.py`).

## First steps
1. Sign in as `admin` and change the password (**Settings → Your account**).
2. **Settings → Users → Add user** for your team: Finance Admins (at least two, because a change needs a second person to approve it), Reviewers, Auditors, and Agent logins linked to an agent code.
3. **Imports → Import a GSC data pack**: the `.xlsx` workbook with the `Jobs`, `Agent_Tasks`, `Agents` and `Rates_and_Details` sheets, or its single-file CSV form (`python tools/workbook_to_csv.py <workbook.xlsx> data/<name>.csv`).
4. If the work is dated later than today (or you are closing a past period), set **Settings → Business date**.
5. Work the **Review Centre**, then **Invoices → Generate** with the pay-run cut-off ("verified on or before") and the invoice dates.
6. **Invoices** offers downloads: a PDF of any invoice (button on each row and on the invoice page), a ZIP of every PDF in a batch (filter by batch), and the invoice list as CSV. Agents can download their own invoices from the agent portal.

## Importing the GSC workbook
* **First import** creates the clients (with payment terms), agents (VAT, self-billing, lead-company payees), self-billing agreements, the VAT rate, company details for invoices, invoice numbering (continuing from the workbook's next INV-/SB- numbers) and one rate card per client from `Rates_and_Details`.
* **Every import** adds new agent tasks as jobs, updates tasks that changed at source (e.g. pending → verified), and re-runs Lane A. Already-invoiced tasks are never changed: they are listed for a credit/debit note.
* **Prices are never changed by a later import.** Differences between the workbook's rates and the rates in force are listed on the import page so a Finance Admin can raise a governed proposal.
* **Mapping:** one FlowGuard job per agent task. The client's fixed job price sits on the completed task of the category's lead role (the role present on most of that category's jobs); other roles on the same job carry a £0 client side, so the client is billed once per job. Failed Visit / No Show tasks have no rate and always go to a person. The mapping is stored in the `workbook_categories` setting so it stays stable between imports.
* **Confidential data stays out of git.** `data/`, `*.xlsx` and the data-pack test report are git-ignored. Tests that need the real workbook look for `FLOWGUARD_TEST_WORKBOOK` or a `GSC*DataPack*.xlsx` in the project root or `data/`, and are skipped when it is absent.
* **Checks:** `python -m app.datapack <workbook.xlsx | .csv>` loads a workbook into a scratch database, runs the full pipeline and compares every task with an independent calculator built from the rate sheet. It writes `docs/DATAPACK_TEST_REPORT.md`.

## Tests
```bash
cd backend && python -m pytest -q tests
```
Tests covering the financial invariants (run against a synthetic fixture in `tests/demo_fixture.py` that the application never loads), the workbook importer, price factors and the data-pack reconciliation: unverified/rejected never READY (even with every override); exactly one current state; READY has a valid rate and full trace; no invoice for BLOCKED/NEEDS_REVIEW/unverified (even if a decision row is tampered); duplicate invoice prevented in code **and** by a DB unique constraint; correct rate-card version by effective date; decimal-only money (floats rejected); historical invoices reproducible after config changes; agents cannot read other agents' jobs or invoices (404, no leak); server-side RBAC and CSRF; AI cannot activate config or override controls; prompt injection in job notes has no effect; AI offline does not stop processing; conflicts block activation; four-eyes needs different users; rollback creates a new version; audit is append-only at DB level; reconciliation detects tampering; invalid CSV rows never enter processing; CSV formula injection escaped.

## Architecture
```
Data sources (GSC workbook, CSV upload, REST/PostgreSQL/MySQL/MSSQL adapters*)
 → adapters + field mapping → normalised job schema → validation / quarantine
 → IMMUTABLE SAFETY CONTROLS (code) → versioned business configuration
 → Lane A pricing → readiness → READY | BLOCKED | NEEDS_REVIEW
 → READY only → Lane B invoice engine → client + agent invoices
 → reconciliation → configurable Sage-ready export
AI (Ollama) sits BESIDE this path behind a tool gateway: read tools + proposal/simulation tools only.
```
\* integration-ready interfaces, not connected. Full design: `docs/ARCHITECTURE.md`.

```
backend/app/
  engine/      pure deterministic core: pricing.py, conflicts.py, vat.py, reason_codes.py, types.py
  services/    processing, invoicing, reconciliation, reconstruction, config_service (proposals,
               simulation, four-eyes, rollback), review, imports (+shadow), analytics, export, automation, queries, audit
  ai/          ollama.py (client), intent.py (NL → structured intent, no SQL), gateway.py (tool allow-list)
  api/         router.py (framework-free dispatch: auth, CSRF, RBAC), routes.py (endpoints)
  security.py  PBKDF2 hashing, opaque sessions (hash stored), RBAC matrix, rate limit, secure headers
  models.py    ~30 tables; Money type = NUMERIC/exact decimal; constraints
  workbook.py  GSC workbook importer      setup.py  first-run admin      main.py  FastAPI wrapper (cookies, headers)
frontend/src   vanilla JS SPA (no build toolchain); tools/build.py assembles index.html
```

## How CSV import and Shadow Mode work
Upload → quarantine (extension, size, binary check, safe filename, duplicate header check) → column mapping (saved templates; synonyms auto-suggested) → schema/type validation per row (required fields, dates, booleans, unknown clients/agents, duplicate IDs, future dates) → preview → summary with downloadable invalid rows → **Shadow Mode** prices valid rows in memory against live configuration and compares with the legacy status/amount. Shadow Mode never sends invoices, never writes to the source, and creates no live jobs. Optionally, "Import valid rows as live jobs" adds them as a normal source.

Path to production: historical CSV → read-only API/DB connection → live shadow mode → controlled Finance pilot → Sage integration → approved straight-through processing.

## Price factors (admin-defined fields)
**Configuration → Price factors** lets a Finance Admin or Super Admin add anything else that changes a price: a yes/no (parking permit), a number (floors climbed, miles) or a pick-list (access type). Defining a factor changes no price. Money moves only when a pricing rule that uses it goes through the normal proposal → simulation → four-eyes approval flow, e.g. "+£2.50 client / +£1.50 agent per floor over 2, all job types". Values are recorded on the job page (audited, re-priced at once) or mapped from a CSV column on import. A factor marked *required* holds jobs without a value as `MISSING_REQUIRED_DATA`; the engine never guesses. Invoiced jobs are frozen, and a factor used by an active rule cannot be retired. Code: `engine/factors.py`, `services/fields.py`, tests in `tests/test_price_factors.py`.

## How AI is governed
* The LLM is never in the calculation path. Prices, VAT, totals and statuses come from `engine/` only.
* Natural language becomes a **structured intent** from a fixed allow-list (`ai/intent.py`), validated server-side; no SQL is ever generated. If Ollama is down, times out, returns malformed JSON or an invalid tool, a deterministic parser and templated explanations take over.
* Tool gateway (`ai/gateway.py`): every call checks the user's role, validates arguments, returns role-restricted data and is logged in `ai_interactions` (prompt hash + 60-char preview only, tools, records, model, outcome, `financial_changes = NONE`).
* Requests to mark jobs ready, approve, override controls, alter totals, delete or activate anything are rejected and audited. Agents only get tools scoped to their own records.
* Job notes, CSV contents and other external text are untrusted data: wrapped as `<untrusted_data>` for the model, never interpreted as instructions, and unable to influence status (tested).
* Prompt-to-configure creates a **proposal**, which goes through validation, conflict check, impact simulation and four-eyes approval like any human proposal.
* Automation learns only from structured review outcomes (≥10 reviews, ≥95% agreement) and can only create a **draft** rule.

## Security model
Server-side RBAC on every endpoint (matrix in `security.py`, visible in Settings) · PBKDF2-HMAC-SHA256 (600k iterations) · opaque session tokens, only SHA-256 hashes stored · HttpOnly, SameSite=Strict, Secure (in production) cookies + CSRF header · login rate limiting · parameterised ORM queries only · output escaping in the UI · CSP, X-Frame-Options, nosniff, Referrer-Policy, HSTS behind HTTPS · upload limits and quarantine · CSV formula-injection escaping on export · audit/AI tables append-only via DB triggers · secrets scrubbed from audit details · no secrets in frontend code; `.env.example` only · agents get 404 (not 403) for other agents' records. Production still needs: HTTPS termination, MFA provider (MFA-ready flag exists), managed secrets, encryption at rest, backups, monitoring.

## Traceability design
Each decision records the contract version, rate-card version, matched rules, modifiers, precedence, VAT config version, readiness checks and engine version as an ordered trace. Invoices store the config snapshot. **Historical reconstruction** re-runs the engine with exactly those versions (rule rows are immutable; retired versions are kept) and reports `REPRODUCED SUCCESSFULLY` or `MISMATCH`.

## Business rules applied
The company charges the configured VAT rate on all sales; agent VAT follows the payee's registration status and date (registered without a VAT number → `VAT_STATUS_UNCLEAR`); VAT is calculated once on the invoice net; a self-billing agreement is required when the payee is VAT registered on the task date; payees who send their own invoices get a purchase record (PI-) rather than a self-bill (SB-); sub-agents are paid through their lead company; weekend and emergency uplifts are mutually exclusive unless a precedence rule exists; PO requirement is per client; one client invoice per client and one agent invoice per payee per batch; default Sage nominal codes 4000/5000 and tax codes T1/T9 (adjust in the export mapping).

## Known limitations
* REST API, PostgreSQL/MySQL/MSSQL source connectors and the Sage API are integration-ready interfaces, not live connections.
* No credit/debit-note workflow yet: when a retroactive change affects issued invoices, FlowGuard reports them and changes nothing.
* MFA is a readiness flag only; no TOTP/IdP integration.
* Single-process rate limiting and error counters (in-memory).
* No password-reset email or self-registration: a Super Admin creates users and resets passwords.
* Analytics query in Python over the live set; fine for thousands of jobs, would move to SQL aggregates at scale.

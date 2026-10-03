# FlowGuard architecture, schema, RBAC and API

## Layers
1. **Sources** – GSC workbook upload (working), CSV upload (working), REST / PostgreSQL / MySQL / MSSQL (integration-ready).
2. **Adapters + field mapping** – source fields → normalised `jobs` schema (`services/imports.py`, mapping templates).
3. **Validation / quarantine** – `import_rows` with per-row errors; nothing invalid is processed.
4. **Immutable safety controls** – `engine/pricing.py: SAFETY_CONTROLS` (SC-01…SC-10), plus DB constraints and triggers.
5. **Versioned business configuration** – contracts, rate-card versions + pricing rules, VAT configuration, client PO requirement, self-billing agreements, settings (four-eyes policy).
6. **Lane A** – `price_job(JobFacts, Snapshot) → Decision`, pure and deterministic.
7. **Readiness** – PO, VAT, self-billing, possible duplicate; collected, then exactly one state.
8. **Lane B** – `services/invoicing.py`, re-checks every safety control at write time.
9. **Reconciliation** – `services/reconciliation.py`.
10. **Export** – `services/export.py`, configurable column mapping.

AI (`ai/`) sits beside layers 5–9: read tools, proposal and simulation tools only.

## Processing flow (Lane A)
verification gate (REJECTED / UNVERIFIED → BLOCKED) → already invoiced / re-submitted invoiced job (DUPLICATE_INVOICE) → exact duplicate (DUPLICATE_JOB, first copy wins) → required data (MISSING_REQUIRED_DATA) → date sanity (INVALID_DATE) → client (UNKNOWN_CLIENT) → one contract in force (CONTRACT_NOT_FOUND / RULE_CONFLICT) → one rate-card version in force by job date (RATE_NOT_FOUND / RULE_CONFLICT) → one base rule for the job type, unless a reviewer selected one (RATE_NOT_FOUND / MULTIPLE_RATE_MATCH / AGENT_RATE_MISSING) → modifiers with exclusivity groups and precedence (MODIFIER_CONFLICT) → nets → PO (MISSING_PO, BLOCKED) → VAT config + agent VAT status (VAT_STATUS_UNCLEAR) → self-billing agreement → possible duplicate by agent/date/normalised postcode/type (POSSIBLE_DUPLICATE, never automatic) → status = BLOCKED if any blocking reason, else NEEDS_REVIEW if any reason, else READY.

## Main entities
users, sessions · clients, agents, self_billing_agreements · contracts, job_types, rate_cards, rate_card_versions, pricing_rules, vat_configurations, settings · jobs, job_overrides, job_decisions, decision_reasons, exceptions, review_decisions · invoice_batches, invoices, invoice_lines (unique job_id+kind) · config_proposals, config_approvals (unique proposal+approver) · import_batches, import_rows, mapping_templates, integrations · reconciliation_runs · audit_events, ai_interactions (append-only), automation_opportunities, notifications.

## RBAC (summary; full matrix in the app under Settings)
| Capability | SUPER_ADMIN | FINANCE_ADMIN | REVIEWER | AUDITOR | AGENT |
|---|---|---|---|---|---|
| Dashboard, jobs, reviews, invoices (read) | ✓ | ✓ | ✓ | ✓ | own only |
| Decide exceptions | | ✓ | ✓ | | |
| Generate invoices | | ✓ | | | |
| Sage export | | ✓ | | ✓ | |
| Propose / approve configuration | ✓ | ✓ | | | |
| Imports | ✓ | ✓ | | read | |
| Users, integrations, four-eyes policy | ✓ | | | | |
| Audit trail, AI log | ✓ | ✓ | | ✓ | |
| AI assistant | ✓ | ✓ | ✓ | ✓ | own-scoped tools |

## API (all under /api; JSON; CSRF header on writes)
auth: `POST auth/login`, `POST auth/logout`, `GET auth/me` · `GET meta`, `GET rbac` ·
`GET dashboard`, `GET money-map` · `GET jobs`, `GET jobs/{id}`, `POST jobs/process`, `POST jobs/{id}/explain` ·
`GET reviews`, `GET reviews/{id}`, `POST reviews/{id}/decide` ·
`GET invoices`, `GET invoices/eligible`, `POST invoices/generate`, `GET invoices/{id}`, `GET invoices/{id}/reconstruct`, `GET batches` ·
`GET reconciliation`, `POST reconciliation/run`, `GET exports/sage`, `GET exports/mapping` ·
`GET clients`, `GET agents`, `GET contracts`, `GET rate-cards`, `GET rules`, `PUT settings/four-eyes` ·
`GET config/proposals`, `GET config/proposals/{id}`, `POST config/simulate`, `POST config/proposals`, `POST config/proposals/{id}/decide` ·
`GET imports`, `GET imports/{id}`, `POST imports`, `POST imports/{id}/map`, `POST imports/{id}/shadow`, `POST imports/{id}/live`, `GET imports/{id}/errors.csv` ·
`GET integrations`, `GET audit`, `GET observability`, `GET users` ·
`GET ai/status`, `POST ai/chat`, `GET ai/log`, `GET automation`, `POST automation/analyse`, `GET automation/{id}/cases`, `POST automation/{id}/reject`, `POST automation/{id}/draft` ·
`GET notifications`, `POST notifications/read` · agent: `GET agent/dashboard`, `GET agent/profile`.

## AI tool boundary
Allowed tools: search_jobs, get_job, get_job_trace, search_invoices, get_invoice, reconstruct_invoice, get_money_map, analyse_exceptions, explain_dashboard, get_configuration, create_config_proposal, simulate_config_change, find_automation_opportunities. Nothing else exists for the model to call: no SQL, no writes to jobs, decisions, invoices or controls, no activation.

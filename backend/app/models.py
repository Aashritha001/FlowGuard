"""Relational data model. Money columns use the Money type (NUMERIC / exact decimal), never float."""
from datetime import datetime, date

from sqlalchemy import (String, Integer, Boolean, Date, DateTime, ForeignKey, JSON, Text,
                        UniqueConstraint, Index, CheckConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, Money


def now() -> datetime:
    """Wall-clock timestamp (UTC) for records and the audit trail. The business date is separate (snapshot.today)."""
    return datetime.utcnow().replace(microsecond=0)


# ---------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(24))  # SUPER_ADMIN, FINANCE_ADMIN, REVIEWER, AUDITOR, AGENT
    password_hash: Mapped[str] = mapped_column(String(255))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (CheckConstraint("role IN ('SUPER_ADMIN','FINANCE_ADMIN','REVIEWER','AUDITOR','AGENT')"),)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # only the hash is stored
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


# ---------------------------------------------------------------- parties
class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    sector: Mapped[str] = mapped_column(String(60), default="")
    po_required: Mapped[bool] = mapped_column(Boolean, default=False)
    billing_email: Mapped[str] = mapped_column(String(160), default="")
    sage_account_ref: Mapped[str] = mapped_column(String(16), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # legacy column, unused


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    supplier_type: Mapped[str] = mapped_column(String(24))  # LIMITED_COMPANY | SOLE_TRADER
    vat_status: Mapped[str] = mapped_column(String(16))      # REGISTERED | NOT_REGISTERED | UNKNOWN
    vat_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    self_billing: Mapped[bool] = mapped_column(Boolean, default=True)
    region: Mapped[str] = mapped_column(String(60), default="")
    sage_supplier_ref: Mapped[str] = mapped_column(String(16), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # legacy column, unused
    paid_via_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)  # lead agent company
    vat_registered_from: Mapped[date | None] = mapped_column(Date, nullable=True)


class SelfBillingAgreement(Base):
    __tablename__ = "self_billing_agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    reference: Mapped[str] = mapped_column(String(40))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")


# ---------------------------------------------------------------- commercial configuration (versioned)
class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    code: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    terms: Mapped[dict] = mapped_column(JSON, default=dict)  # payment days, billing address, clause references
    __table_args__ = (UniqueConstraint("code", "version"),)


class JobType(Base):
    __tablename__ = "job_types"
    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class RateCard(Base):
    __tablename__ = "rate_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    contract_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    versions: Mapped[list["RateCardVersion"]] = relationship(back_populates="rate_card", order_by="RateCardVersion.version")


class RateCardVersion(Base):
    """Never edited once ACTIVE. Changes create a new version; history is kept for reproduction."""
    __tablename__ = "rate_card_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_card_id: Mapped[int] = mapped_column(ForeignKey("rate_cards.id"))
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # DRAFT, PENDING, ACTIVE, RETIRED, REJECTED
    precedence: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["EMERGENCY>WEEKEND"]
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("config_proposals.id"), nullable=True)
    rollback_of_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    rate_card: Mapped[RateCard] = relationship(back_populates="versions")
    rules: Mapped[list["PricingRule"]] = relationship(back_populates="rcv", order_by="PricingRule.rule_code")
    __table_args__ = (UniqueConstraint("rate_card_id", "version"),)


class PricingRule(Base):
    __tablename__ = "pricing_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    rcv_id: Mapped[int] = mapped_column(ForeignKey("rate_card_versions.id"))
    rule_code: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(12))  # BASE | MODIFIER | AGENT_RATE
    job_type: Mapped[str] = mapped_column(String(24))  # '*' for modifiers that apply to all types
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)  # WEEKEND, EMERGENCY, REVISIT, F:<key>[=<choice>]
    group: Mapped[str | None] = mapped_column(String(24), nullable=True)  # mutually-exclusive uplift group
    client_amount: Mapped[object] = mapped_column(Money)
    agent_amount: Mapped[object] = mapped_column(Money)
    clause_ref: Mapped[str] = mapped_column(String(40), default="")
    agent_code: Mapped[str | None] = mapped_column(String(16), nullable=True)  # AGENT_RATE: whose special rate
    included_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # time-based agent pay
    agent_extra_per_30: Mapped[object] = mapped_column(Money, nullable=True)
    included_units: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NUMBER price factor: free units
    rcv: Mapped[RateCardVersion] = relationship(back_populates="rules")


class PriceField(Base):
    """An admin-defined price factor: a job attribute that pricing rules can depend on (e.g. 'Parking permit',
    'Floors climbed', 'Access type'). Defining one never changes money; only an approved pricing rule that uses it does."""
    __tablename__ = "price_fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(12))  # BOOLEAN | NUMBER | CHOICE
    choices: Mapped[list] = mapped_column(JSON, default=list)
    unit: Mapped[str] = mapped_column(String(24), default="")
    required_for: Mapped[list] = mapped_column(JSON, default=list)  # job types that must have a value; ["*"] = all
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")  # ACTIVE | RETIRED
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (CheckConstraint("kind IN ('BOOLEAN','NUMBER','CHOICE')"),)


class VATConfiguration(Base):
    __tablename__ = "vat_configurations"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    standard_rate: Mapped[str] = mapped_column(String(8))  # stored as exact string e.g. "0.20"
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    note: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Business configuration flags (NOT safety controls - those are code constants)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


# ---------------------------------------------------------------- jobs and decisions
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_job_id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    job_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(16))  # VERIFIED, UNVERIFIED, REJECTED
    verification_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weekend: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    revisit: Mapped[bool] = mapped_column(Boolean, default=False)
    occupancy_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(12), nullable=True)
    minutes_on_site: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # values of admin-defined price factors
    notes: Mapped[str] = mapped_column(Text, default="")  # UNTRUSTED free text
    source_system: Mapped[str] = mapped_column(String(32), default="MANUAL")
    source_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)  # shadow-mode jobs can never be invoiced
    demo_scenario: Mapped[str] = mapped_column(String(60), default="")  # free-text tags (column name kept for compatibility)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (Index("ix_jobs_ext", "source_system", "external_job_id"), Index("ix_jobs_date", "job_date"))


class JobOverride(Base):
    """Structured, audited human resolutions the deterministic engine consumes (e.g. chosen rule)."""
    __tablename__ = "job_overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    kind: Mapped[str] = mapped_column(String(32))  # SELECT_RULE, NOT_DUPLICATE, CONFIRMED_DUPLICATE, PRECEDENCE
    value: Mapped[dict] = mapped_column(JSON)
    review_decision_id: Mapped[int | None] = mapped_column(ForeignKey("review_decisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobDecision(Base):
    __tablename__ = "job_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(16))
    client_net: Mapped[object] = mapped_column(Money, nullable=True)
    agent_net: Mapped[object] = mapped_column(Money, nullable=True)
    value_at_stake: Mapped[object] = mapped_column(Money, nullable=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    rcv_id: Mapped[int | None] = mapped_column(ForeignKey("rate_card_versions.id"), nullable=True)
    vat_config_id: Mapped[int | None] = mapped_column(ForeignKey("vat_configurations.id"), nullable=True)
    matched_rules: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    engine_version: Mapped[str] = mapped_column(String(16))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)  # True once invoiced
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    reasons: Mapped[list["DecisionReason"]] = relationship(back_populates="decision")
    __table_args__ = (CheckConstraint("status IN ('READY','BLOCKED','NEEDS_REVIEW')"),
                      Index("ix_dec_current", "job_id", "is_current"))


class DecisionReason(Base):
    __tablename__ = "decision_reasons"
    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("job_decisions.id"))
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    team: Mapped[str] = mapped_column(String(40), default="")
    decision: Mapped[JobDecision] = relationship(back_populates="reasons")


class ExceptionCase(Base):
    __tablename__ = "exceptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("job_decisions.id"))
    status_kind: Mapped[str] = mapped_column(String(16))  # BLOCKED | NEEDS_REVIEW
    reason_code: Mapped[str] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(10))  # CRITICAL, HIGH, NORMAL
    value_at_stake: Mapped[object] = mapped_column(Money, nullable=True)
    team: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN, RESOLVED, SUPERSEDED
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewDecision(Base):
    """Structured human review outcomes. These are what automation-opportunity analysis learns from."""
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    reviewer: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"pattern":"WEEKEND+EMERGENCY","resolution":"EMERGENCY>WEEKEND"}
    reason: Mapped[str] = mapped_column(Text)
    evidence_ref: Mapped[str] = mapped_column(String(80), default="")
    old_status: Mapped[str] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- invoices
class InvoiceBatch(Base):
    __tablename__ = "invoice_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expected: Mapped[dict] = mapped_column(JSON, default=dict)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # CLIENT (sales) | AGENT (purchase / self-bill)
    batch_id: Mapped[int] = mapped_column(ForeignKey("invoice_batches.id"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    issue_date: Mapped[date] = mapped_column(Date)
    net: Mapped[object] = mapped_column(Money)
    vat: Mapped[object] = mapped_column(Money)
    gross: Mapped[object] = mapped_column(Money)
    vat_rate: Mapped[str] = mapped_column(String(8))
    vat_treatment: Mapped[str] = mapped_column(String(40))
    self_billed: Mapped[bool] = mapped_column(Boolean, default=False)
    self_billing_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    po_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="ISSUED")
    exported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", order_by="InvoiceLine.id")
    __table_args__ = (CheckConstraint("kind IN ('CLIENT','AGENT')"),)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    kind: Mapped[str] = mapped_column(String(8))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("job_decisions.id"))
    description: Mapped[str] = mapped_column(String(240))
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    net: Mapped[object] = mapped_column(Money)
    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    # Duplicate-invoice protection enforced by the database: a job can appear on at most one
    # client invoice line and one agent invoice line, ever.
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_one_line_per_job_per_side"),)


# ---------------------------------------------------------------- configuration governance
class ConfigProposal(Base):
    __tablename__ = "config_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # RATE_CHANGE, MODIFIER_CHANGE, PO_REQUIREMENT, PRECEDENCE_RULE, ROLLBACK
    title: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(8), default="HUMAN")  # HUMAN | AI
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT, PENDING_APPROVAL, ACTIVE, REJECTED, BLOCKED_CONFLICT
    risk_level: Mapped[str] = mapped_column(String(8), default="NORMAL")
    risk_reasons: Mapped[list] = mapped_column(JSON, default=list)
    approvals_required: Mapped[int] = mapped_column(Integer, default=1)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    simulation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    resulting_rcv_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ConfigApproval(Base):
    __tablename__ = "config_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("config_proposals.id"))
    approver: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(10))  # APPROVE | REJECT
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("proposal_id", "approver"),)


# ---------------------------------------------------------------- imports / integrations
class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(120))
    uploaded_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="QUARANTINED")  # QUARANTINED, MAPPED, VALIDATED, SHADOW_COMPLETE
    headers: Mapped[list] = mapped_column(JSON, default=list)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    shadow_result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))
    row_no: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON)
    mapped: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # VALID, INVALID, PENDING
    errors: Mapped[list] = mapped_column(JSON, default=list)


class MappingTemplate(Base):
    __tablename__ = "mapping_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    mapping: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(24))  # CSV, REST_API, POSTGRESQL, MYSQL, MSSQL, SAGE
    status: Mapped[str] = mapped_column(String(24))  # WORKING, INTEGRATION_READY, MOCK
    access_mode: Mapped[str] = mapped_column(String(16), default="READ_ONLY")
    description: Mapped[str] = mapped_column(Text, default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    result: Mapped[dict] = mapped_column(JSON)
    mismatch_count: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- audit / AI / notifications
class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(24), default="")
    action: Mapped[str] = mapped_column(String(48))
    entity: Mapped[str] = mapped_column(String(32), default="")
    entity_id: Mapped[str] = mapped_column(String(48), default="")
    old_value: Mapped[str] = mapped_column(String(120), default="")
    new_value: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    config_version: Mapped[str] = mapped_column(String(40), default="")
    ai_involvement: Mapped[str] = mapped_column(String(40), default="None")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AIInteraction(Base):
    __tablename__ = "ai_interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(24))
    request_type: Mapped[str] = mapped_column(String(40))
    prompt_sha256: Mapped[str] = mapped_column(String(64))  # raw prompt is NOT retained
    prompt_preview: Mapped[str] = mapped_column(String(80), default="")
    tools_called: Mapped[list] = mapped_column(JSON, default=list)
    records_accessed: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(40))
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_transmission: Mapped[str] = mapped_column(String(16), default="NONE")
    financial_changes: Mapped[str] = mapped_column(String(16), default="NONE")


class AutomationOpportunity(Base):
    __tablename__ = "automation_opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(80))
    reason_code: Mapped[str] = mapped_column(String(40))
    resolution: Mapped[str] = mapped_column(String(80))
    sample_size: Mapped[int] = mapped_column(Integer)
    agreement: Mapped[int] = mapped_column(Integer)
    review_ids: Mapped[list] = mapped_column(JSON, default=list)
    open_matching: Mapped[int] = mapped_column(Integer, default=0)
    est_monthly_avoided: Mapped[int] = mapped_column(Integer, default=0)
    stp_before: Mapped[str] = mapped_column(String(10), default="")
    stp_after: Mapped[str] = mapped_column(String(10), default="")
    proposed_rule: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="SUGGESTED")  # SUGGESTED, REJECTED, DRAFT_RULE_CREATED
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(String(24), default="STAFF")  # STAFF or AGENT:<id>
    read: Mapped[bool] = mapped_column(Boolean, default=False)

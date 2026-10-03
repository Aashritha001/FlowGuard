"""Extensible reason-code catalogue. Each code has a deterministic human-readable template so that
every BLOCKED / NEEDS_REVIEW result is explainable without AI."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonDef:
    code: str
    outcome: str      # BLOCKED or NEEDS_REVIEW
    priority: str     # CRITICAL, HIGH, NORMAL
    team: str
    title: str
    template: str     # str.format(**facts)
    agent_template: str  # what an agent may be told (no internal configuration)
    leak_bucket: str  # which money-at-risk bucket on the dashboard


R = ReasonDef
CATALOGUE: dict[str, ReasonDef] = {r.code: r for r in [
    R("UNVERIFIED_JOB", "BLOCKED", "NORMAL", "Operations", "Job not verified",
      "Job {job_ref} has not been verified by a GSC administrator. Unverified jobs can never become invoice-ready.",
      "This job is waiting for GSC to verify it. Nothing can be invoiced until it is verified.", "other"),
    R("REJECTED_JOB", "BLOCKED", "NORMAL", "Operations", "Job rejected at verification",
      "Job {job_ref} was rejected at verification by {verified_by}. Rejected jobs can never be invoiced.",
      "This job was not accepted at verification, so it will not be invoiced.", "other"),
    R("DUPLICATE_JOB", "BLOCKED", "HIGH", "Finance", "Duplicate job ID",
      "Job {job_ref} has the same external ID and source as job {other_ref}, which was received first. It has been held to stop the same work being billed twice.",
      "This job appears twice in the system. Finance is holding the duplicate copy.", "duplicates"),
    R("DUPLICATE_INVOICE", "BLOCKED", "CRITICAL", "Finance", "Already invoiced",
      "Job {job_ref} is already on invoice {invoice_no}. A job can be invoiced only once on each side.",
      "This job has already been invoiced on {invoice_no}.", "duplicates"),
    R("POSSIBLE_DUPLICATE", "NEEDS_REVIEW", "HIGH", "Finance", "Possible duplicate",
      "Job {job_ref} closely matches job {other_ref} (same agent, date, postcode and job type) but has a different job ID. FlowGuard does not decide duplicates on similarity; a person must confirm.",
      "Finance is checking whether this job is a repeat of another visit. No invoice has been generated yet.", "duplicates"),
    R("MISSING_REQUIRED_DATA", "NEEDS_REVIEW", "NORMAL", "Operations", "Missing required data",
      "Job {job_ref} is missing required field(s): {fields}. FlowGuard will not fill these in.",
      "Some details on this job are missing ({fields}). GSC is checking them.", "other"),
    R("INVALID_DATE", "NEEDS_REVIEW", "NORMAL", "Operations", "Invalid job date",
      "Job {job_ref} has job date {job_date}, which is {problem}.",
      "The date recorded for this job needs checking.", "other"),
    R("UNKNOWN_CLIENT", "NEEDS_REVIEW", "HIGH", "Finance", "Unknown client",
      "Job {job_ref} references a client that is not configured in FlowGuard.",
      "Finance is checking the client details on this job.", "missing_rate"),
    R("CONTRACT_NOT_FOUND", "NEEDS_REVIEW", "HIGH", "Commercial", "No contract in force",
      "No contract for {client_name} is in force on {job_date}.",
      "Finance is reviewing the applicable rate for this job. No invoice has been generated yet.", "missing_rate"),
    R("RATE_NOT_FOUND", "NEEDS_REVIEW", "HIGH", "Commercial", "No valid rate",
      "{client_name} has no {job_type} rate in force on {job_date} (rate card {rate_card}). FlowGuard will not guess a price.",
      "Finance is reviewing the applicable rate for this job. No invoice has been generated yet.", "missing_rate"),
    R("MULTIPLE_RATE_MATCH", "NEEDS_REVIEW", "HIGH", "Commercial", "Multiple valid rates",
      "Two or more pricing rules ({rules}) are valid for {job_type} on {job_date}. FlowGuard has not selected either because doing so could produce an incorrect invoice.",
      "Finance is reviewing the applicable rate for this job. No invoice has been generated yet.", "overlapping_rules"),
    R("MODIFIER_CONFLICT", "NEEDS_REVIEW", "NORMAL", "Commercial", "Conflicting modifiers",
      "Job {job_ref} qualifies for modifiers {rules} in the same uplift group and rate card {rate_card} has no precedence rule saying which applies.",
      "Finance is confirming which uplift applies to this job. No invoice has been generated yet.", "overlapping_rules"),
    R("RULE_CONFLICT", "NEEDS_REVIEW", "CRITICAL", "Commercial", "Rule conflict",
      "More than one active rate-card version for {client_name} covers {job_date} ({versions}). Configuration must be corrected before this job can be priced.",
      "Finance is reviewing the applicable rate for this job. No invoice has been generated yet.", "overlapping_rules"),
    R("MISSING_PO", "BLOCKED", "HIGH", "Finance", "Missing purchase order",
      "This job cannot become invoice-ready because {client_name} requires a purchase-order reference and none was provided.",
      "The client needs a purchase-order number before this job can be invoiced. GSC is obtaining it.", "missing_po"),
    R("VAT_STATUS_UNCLEAR", "NEEDS_REVIEW", "CRITICAL", "Finance", "VAT status unclear",
      "Agent {agent_name}'s VAT status is {vat_status}{vat_detail}. FlowGuard will not decide VAT treatment without a confirmed status.",
      "Finance needs to confirm your VAT details before your invoice can be produced.", "vat"),
    R("SELF_BILLING_AGREEMENT_MISSING", "NEEDS_REVIEW", "HIGH", "Finance", "Self-billing agreement missing",
      "Agent {agent_name} is set up for self-billing but has no self-billing agreement valid on {job_date}.",
      "Finance needs a current self-billing agreement with you before your invoice can be produced.", "other"),
    R("AGENT_RATE_MISSING", "NEEDS_REVIEW", "HIGH", "Commercial", "Agent pay rate missing",
      "Rule {rules} has a client price but no agent pay amount. Both sides of the job must be priced.",
      "Finance is reviewing the pay rate for this job.", "missing_rate"),
]}


def get(code: str) -> ReasonDef:
    return CATALOGUE[code]


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def render(code: str, facts: dict, audience: str = "staff") -> str:
    d = CATALOGUE[code]
    t = d.agent_template if audience == "agent" else d.template
    return t.format_map(_Safe({k: ("" if v is None else v) for k, v in facts.items()}))

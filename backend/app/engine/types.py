"""Plain data structures the deterministic engine consumes. No database, no AI, no I/O."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class JobFacts:
    id: int
    ref: str
    external_job_id: str
    source_system: str
    client_id: int | None
    agent_id: int | None
    job_type: str | None
    job_date: date | None
    verification_status: str
    verified_by: str | None
    weekend: bool
    emergency: bool
    revisit: bool
    po_number: str | None
    postcode: str | None
    created_order: int  # tie-break for "which copy arrived first"
    minutes_on_site: int | None = None
    attributes: dict | None = None  # admin-defined price factor values, keyed by field key


@dataclass
class RuleFacts:
    id: int
    code: str
    kind: str
    job_type: str
    condition: str | None
    group: str | None
    client_amount: Decimal | None
    agent_amount: Decimal | None
    clause_ref: str = ""
    agent_code: str | None = None             # AGENT_RATE rules: special agent rate for this agent (or its payee)
    included_minutes: int | None = None       # time-based agent pay: minutes covered by agent_amount
    agent_extra_per_30: Decimal | None = None  # ...then this much per 30 minutes or part of 30 minutes
    included_units: int | None = None          # NUMBER price factor: units covered before the per-unit amount applies


@dataclass
class RCVFacts:
    id: int
    rate_card_id: int
    rate_card_name: str
    client_id: int
    version: int
    valid_from: date
    valid_to: date | None
    status: str
    precedence: list
    rules: list[RuleFacts]


@dataclass
class ContractFacts:
    id: int
    client_id: int
    code: str
    version: int
    valid_from: date
    valid_to: date | None
    status: str


@dataclass
class ClientFacts:
    id: int
    code: str
    name: str
    po_required: bool


@dataclass
class AgentFacts:
    id: int
    code: str
    name: str
    supplier_type: str
    vat_status: str
    vat_number: str | None
    self_billing: bool
    paid_via_id: int | None = None          # lead agent company that is paid for this agent's work
    vat_registered_from: date | None = None


@dataclass
class SBAFacts:
    agent_id: int
    reference: str
    valid_from: date
    valid_to: date | None
    status: str


@dataclass
class FieldFacts:
    key: str
    label: str
    kind: str              # BOOLEAN | NUMBER | CHOICE
    choices: list
    required_for: list     # job types; "*" = every job type


@dataclass
class VATFacts:
    id: int
    version: int
    standard_rate: Decimal
    valid_from: date
    valid_to: date | None
    status: str


@dataclass
class Snapshot:
    """Everything the engine needs, frozen at a moment in time. Simulation edits a copy of this."""
    today: date
    clients: dict[int, ClientFacts]
    agents: dict[int, AgentFacts]
    contracts: list[ContractFacts]
    rcvs: list[RCVFacts]
    vats: list[VATFacts]
    sbas: list[SBAFacts]
    invoiced: dict[int, str] = field(default_factory=dict)            # job_id -> invoice number
    overrides: dict[int, list[dict]] = field(default_factory=dict)    # job_id -> [{kind, value}]
    exact_index: dict[tuple, list] = field(default_factory=dict)      # (source, ext_id) -> [(order, job_id, ref)]
    fuzzy_index: dict[tuple, list] = field(default_factory=dict)      # (agent, date, postcode, type) -> [(order, job_id, ref)]
    fields: dict = field(default_factory=dict)  # key -> FieldFacts (ACTIVE price factors only)
    sba_required_for: str = "ALL"  # business config: "ALL" self-billed payees, or only "VAT_REGISTERED" ones


@dataclass
class Reason:
    code: str
    message: str
    evidence: dict
    rules: list
    team: str
    outcome: str


@dataclass
class Decision:
    job_id: int
    status: str
    client_net: Decimal | None
    agent_net: Decimal | None
    value_at_stake: Decimal | None
    contract_id: int | None
    rcv_id: int | None
    vat_config_id: int | None
    matched_rules: list
    trace: list
    reasons: list[Reason]

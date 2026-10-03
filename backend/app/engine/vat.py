"""Deterministic VAT and invoice-total arithmetic (configuration-driven; confirm treatment with your tax adviser).
The LLM never calls into or replaces anything in this module."""
from decimal import Decimal, ROUND_HALF_UP

PENNY = Decimal("0.01")


def q(v: Decimal) -> Decimal:
    if isinstance(v, float):
        raise TypeError("float not allowed in money arithmetic")
    return Decimal(v).quantize(PENNY, rounding=ROUND_HALF_UP)


def client_vat_rate(vat_cfg_rate: Decimal) -> Decimal:
    # The company is VAT-registered and charges the configured standard rate on all sales.
    return vat_cfg_rate


def agent_vat_rate(vat_status: str, vat_number: str | None, vat_cfg_rate: Decimal,
                   registered_from=None, on=None) -> Decimal | None:
    """Returns None when the status is not safe to decide (caller turns that into VAT_STATUS_UNCLEAR).
    A supply made before the agent's VAT registration date carries no VAT."""
    if vat_status == "REGISTERED" and vat_number and registered_from and on and on < registered_from:
        return Decimal("0")
    if vat_status == "REGISTERED" and vat_number:
        return vat_cfg_rate
    if vat_status == "NOT_REGISTERED":
        return Decimal("0")
    return None


def totals(line_nets: list[Decimal], rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """VAT is calculated once on the invoice net total (HMRC permits invoice-level rounding)."""
    net = q(sum((q(n) for n in line_nets), Decimal("0")))
    vat = q(net * rate)
    return net, vat, q(net + vat)

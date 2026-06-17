"""
TieredPricing — different price per unit depending on the tier the quantity falls into.

This is the "cumulative" / "stacked" tier model, NOT the "volume" model:
    Tiers: [(0, 1000, ₹2.00), (1000, 5000, ₹1.50), (5000, None, ₹1.00)]
    Quantity = 6000:
        First 1000 units  @ ₹2.00 = ₹2000
        Next  4000 units  @ ₹1.50 = ₹6000
        Last  1000 units  @ ₹1.00 = ₹1000
        ------------------------------------
        Total                     = ₹9000

A tier with `to_units = None` is the open-ended top tier.

Tier boundaries are HALF-OPEN on the right: a tier (from, to, price)
covers units strictly less than `to` (i.e. [from, to)).
"""

from dataclasses import dataclass
from typing import Optional

from billing_engine.money import Money
from billing_engine.pricing.base import PricingStrategy


@dataclass(frozen=True)
class Tier:
    from_units: int
    to_units: Optional[int]
    unit_price: Money


class TieredPricing(PricingStrategy):
    """Charges across multiple price tiers based on cumulative quantity."""

    def __init__(self, tiers: list[Tier]) -> None:
        if not tiers:
            raise ValueError("tiers must not be empty")
        # validate currency consistency
        currency = tiers[0].unit_price.currency
        for t in tiers:
            if t.unit_price.currency != currency:
                raise ValueError("All tiers must use the same currency")
        # validate contiguous boundaries
        for i in range(1, len(tiers)):
            if tiers[i].from_units != tiers[i - 1].to_units:
                raise ValueError(f"Tiers are not contiguous at index {i}")
        # top tier must be open-ended
        if tiers[-1].to_units is not None:
            raise ValueError("The top tier must be open-ended (to_units=None)")
        self._tiers = tiers

    def calculate(self, quantity: int) -> Money:
        if quantity < 0:
            raise ValueError("quantity must be non-negative")
        currency = self._tiers[0].unit_price.currency
        total = Money.zero(currency)
        remaining = quantity
        for tier in self._tiers:
            if remaining <= 0:
                break
            if tier.to_units is None:
                units_in_tier = remaining
            else:
                units_in_tier = min(remaining, tier.to_units - tier.from_units)
            total = total + tier.unit_price * units_in_tier
            remaining -= units_in_tier
        return total

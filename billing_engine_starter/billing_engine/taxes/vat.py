"""
VATCalculator — single-rate VAT (e.g. 19% in Germany).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class VATCalculator(TaxCalculator):
    def __init__(self, rate: Decimal) -> None:
        if isinstance(rate, float):
            raise TypeError("rate must be Decimal, not float")
        if not isinstance(rate, Decimal):
            raise TypeError(f"rate must be Decimal, got {type(rate).__name__}")
        if rate < Decimal("0") or rate > Decimal("1"):
            raise ValueError("rate must be in [0, 1]")
        self._rate = rate

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        vat = taxable * self._rate
        percent = self._rate * 100
        label = f"VAT {percent.normalize()}%"
        return TaxBreakdown(components=[(label, vat)], total=vat)

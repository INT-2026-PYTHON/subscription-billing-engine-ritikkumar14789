"""
GSTCalculator — Indian Goods & Services Tax.

The rule:
    - If customer_state == seller_state (or seller_state is "")  =>  intra-state
        -> charge CGST + SGST (split equally, e.g. 9% + 9% = 18%)
    - Else  =>  inter-state
        -> charge IGST (e.g. 18%)

Customers without a state code default to IGST (safe choice).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class GSTCalculator(TaxCalculator):
    def __init__(self, cgst: Decimal, sgst: Decimal, igst: Decimal) -> None:
        for name, rate in [("cgst", cgst), ("sgst", sgst), ("igst", igst)]:
            if isinstance(rate, float):
                raise TypeError(f"{name} must be Decimal, not float")
            if not isinstance(rate, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if rate < Decimal("0") or rate > Decimal("1"):
                raise ValueError(f"{name} must be in [0, 1]")
        if cgst + sgst != igst:
            raise ValueError(f"cgst + sgst must equal igst: {cgst} + {sgst} != {igst}")
        self._cgst = cgst
        self._sgst = sgst
        self._igst = igst

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        intra = bool(context.customer_state) and context.customer_state == context.seller_state
        if intra:
            cgst_amt = taxable * self._cgst
            sgst_amt = taxable * self._sgst
            total = cgst_amt + sgst_amt
            cgst_pct = (self._cgst * 100).normalize()
            sgst_pct = (self._sgst * 100).normalize()
            components = [(f"CGST {cgst_pct}%", cgst_amt), (f"SGST {sgst_pct}%", sgst_amt)]
        else:
            igst_amt = taxable * self._igst
            total = igst_amt
            igst_pct = (self._igst * 100).normalize()
            components = [(f"IGST {igst_pct}%", igst_amt)]
        return TaxBreakdown(components=components, total=total)

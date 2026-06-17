"""
BillingCycle — finds due subscriptions, generates invoices, posts ledger DEBITs,
advances the subscription period. Must be IDEMPOTENT (safe to run twice).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from typing import Callable, Optional

from billing_engine.db import (
    Database,
    CustomerRepository, PlanRepository, SubscriptionRepository,
    UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository,
    LedgerRepository,
)
from billing_engine.models import (
    Subscription, SubscriptionStatus, InvoiceStatus, LedgerEntry, LedgerDirection,
)
from billing_engine.billing.pipeline import build_invoice


@dataclass
class BillingResult:
    invoices_created: int
    invoices_skipped_duplicate: int
    trials_activated: int


class BillingCycle:
    def __init__(
        self,
        db: Database,
        customer_repo: CustomerRepository,
        plan_repo: PlanRepository,
        subscription_repo: SubscriptionRepository,
        usage_repo: UsageRecordRepository,
        invoice_repo: InvoiceRepository,
        line_item_repo: InvoiceLineItemRepository,
        ledger_repo: LedgerRepository,
        strategy_factory: Callable,
        discount_factory: Callable,
        tax_factory: Callable,
    ) -> None:
        self.db = db
        self.customer_repo = customer_repo
        self.plan_repo = plan_repo
        self.subscription_repo = subscription_repo
        self.usage_repo = usage_repo
        self.invoice_repo = invoice_repo
        self.line_item_repo = line_item_repo
        self.ledger_repo = ledger_repo
        self.strategy_factory = strategy_factory
        self.discount_factory = discount_factory
        self.tax_factory = tax_factory

    def run(self, as_of: date) -> BillingResult:
        invoices_created = 0
        invoices_skipped = 0
        trials_activated = 0

        # Activate trials whose trial_end has passed
        for sub in self.subscription_repo.list_all():
            if sub.status == SubscriptionStatus.TRIAL and sub.trial_end and sub.trial_end <= as_of:
                self.subscription_repo.update_status(sub.id, SubscriptionStatus.ACTIVE)
                trials_activated += 1

        # Bill due subscriptions
        for sub in self.subscription_repo.get_due_for_billing(as_of):
            customer = self.customer_repo.get(sub.customer_id)
            plan = self.plan_repo.get(sub.plan_id)
            strategy = self.strategy_factory(plan)
            discount = self.discount_factory(sub.discount_id)
            tax_calc, tax_context = self.tax_factory(customer)

            usage = self.usage_repo.sum_for_period(
                sub.id, "calls", sub.current_period_start, sub.current_period_end
            )
            invoice_count = self.invoice_repo.count_for_subscription(sub.id)

            invoice = build_invoice(
                subscription=sub,
                plan=plan,
                strategy=strategy,
                discount=discount,
                tax_calc=tax_calc,
                tax_context=tax_context,
                usage_quantity=usage,
                period_start=sub.current_period_start,
                period_end=sub.current_period_end,
                invoice_count_so_far=invoice_count,
            )
            invoice.status = InvoiceStatus.ISSUED

            try:
                with self.db.transaction() as conn:
                    # Insert invoice
                    cur = conn.execute(
                        """INSERT INTO invoices
                           (subscription_id, period_start, period_end, currency,
                            subtotal, discount_total, tax_total, total, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            invoice.subscription_id,
                            invoice.period_start.isoformat(),
                            invoice.period_end.isoformat(),
                            invoice.subtotal.currency,
                            invoice.subtotal.to_storage(),
                            invoice.discount_total.to_storage(),
                            invoice.tax_total.to_storage(),
                            invoice.total.to_storage(),
                            invoice.status.value,
                        ),
                    )
                    invoice_id = cur.lastrowid

                    # Insert line items
                    for item in invoice.line_items:
                        conn.execute(
                            "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) VALUES (?, ?, ?, ?)",
                            (invoice_id, item.description, item.amount.to_storage(), item.kind.value),
                        )

                    # Post ledger DEBIT
                    conn.execute(
                        """INSERT INTO ledger_entries (invoice_id, customer_id, amount, currency, direction, reason)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            invoice_id,
                            customer.id,
                            invoice.total.to_storage(),
                            invoice.total.currency,
                            LedgerDirection.DEBIT.value,
                            f"Invoice #{invoice_id} for subscription {sub.id}",
                        ),
                    )

                    # Advance period
                    new_start = sub.current_period_end
                    new_end = new_start + relativedelta(months=1)
                    conn.execute(
                        "UPDATE subscriptions SET current_period_start = ?, current_period_end = ? WHERE id = ?",
                        (new_start.isoformat(), new_end.isoformat(), sub.id),
                    )

                invoices_created += 1

            except sqlite3.IntegrityError:
                invoices_skipped += 1

        return BillingResult(invoices_created, invoices_skipped, trials_activated)

    def upgrade_subscription(self, subscription_id: int, new_plan_id: int, switch_date: date) -> None:
        """Mid-cycle upgrade — Day 4 stretch."""
        from billing_engine.billing.proration import compute_proration
        from billing_engine.models import InvoiceStatus, LineItemKind, InvoiceLineItem
        from billing_engine.money import Money

        sub = self.subscription_repo.get(subscription_id)
        customer = self.customer_repo.get(sub.customer_id)
        old_plan = self.plan_repo.get(sub.plan_id)
        new_plan = self.plan_repo.get(new_plan_id)
        tax_calc, tax_context = self.tax_factory(customer)

        old_strategy = self.strategy_factory(old_plan)
        new_strategy = self.strategy_factory(new_plan)
        old_price = old_strategy.calculate(0)
        new_price = new_strategy.calculate(0)

        proration = compute_proration(
            old_plan_price=old_price,
            new_plan_price=new_price,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            switch_date=switch_date,
            tax_calc=tax_calc,
            tax_context=tax_context,
        )

        credit = proration.credit_amount
        charge = proration.charge_amount
        credit_tax = proration.credit_tax
        charge_tax = proration.charge_tax
        net = (charge + charge_tax) - (credit + credit_tax)
        currency = old_price.currency

        from billing_engine.money import Money
        subtotal = charge - credit
        discount_total = Money.zero(currency)
        tax_total = charge_tax - credit_tax if charge_tax >= credit_tax else Money.zero(currency)

        line_items = [
            InvoiceLineItem(None, None, f"Proration credit: {old_plan.name}", -credit, LineItemKind.PRORATION_CREDIT),
            InvoiceLineItem(None, None, f"Proration charge: {new_plan.name}", charge, LineItemKind.PRORATION_CHARGE),
        ]

        from billing_engine.models import Invoice
        invoice = Invoice(
            id=None,
            subscription_id=sub.id,
            period_start=switch_date,
            period_end=sub.current_period_end,
            subtotal=subtotal,
            discount_total=discount_total,
            tax_total=tax_total if not tax_total.is_negative() else Money.zero(currency),
            total=net,
            status=InvoiceStatus.ISSUED,
            line_items=line_items,
        )

        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO invoices
                   (subscription_id, period_start, period_end, currency,
                    subtotal, discount_total, tax_total, total, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invoice.subscription_id,
                    invoice.period_start.isoformat(),
                    invoice.period_end.isoformat(),
                    currency,
                    invoice.subtotal.to_storage(),
                    invoice.discount_total.to_storage(),
                    invoice.tax_total.to_storage(),
                    invoice.total.to_storage(),
                    invoice.status.value,
                ),
            )
            invoice_id = cur.lastrowid

            for item in line_items:
                conn.execute(
                    "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) VALUES (?, ?, ?, ?)",
                    (invoice_id, item.description, item.amount.to_storage(), item.kind.value),
                )

            conn.execute(
                """INSERT INTO ledger_entries (invoice_id, customer_id, amount, currency, direction, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    invoice_id,
                    customer.id,
                    net.to_storage(),
                    currency,
                    LedgerDirection.DEBIT.value,
                    f"Proration invoice #{invoice_id}: upgrade to {new_plan.name}",
                ),
            )

            conn.execute(
                "UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                (new_plan_id, subscription_id),
            )

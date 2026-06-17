"""
Repositories — the ONLY place SQL lives.

Each repository wraps the Database connection and exposes methods that
take/return domain dataclasses (defined in billing_engine/models/).

⚠️ YOU IMPLEMENT every method body marked TODO.
   The signatures, docstrings, and the LedgerRepository's append-only
   guarantee are already in place — do not change them.

Conventions:
  - Always use parameterized queries (`?` placeholders) — NEVER f-string SQL.
  - Money values are persisted as TEXT using `money.to_storage()`.
  - Dates are persisted as ISO strings (`date.isoformat()`).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from billing_engine.db.database import Database
from billing_engine.money import Money
from billing_engine.models import (
    Customer,
    Plan, PricingType, BillingPeriod,
    Subscription, SubscriptionStatus,
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind,
    LedgerEntry, LedgerDirection,
)


# ============================================================
# CUSTOMERS
# ============================================================
class CustomerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, customer: Customer) -> Customer:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO customers (name, email, country_code, state_code) VALUES (?, ?, ?, ?)",
                (customer.name, customer.email, customer.country_code, customer.state_code),
            )
            return Customer(cur.lastrowid, customer.name, customer.email,
                            customer.country_code, customer.state_code)

    def get(self, customer_id: int) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            return None
        return Customer(row["id"], row["name"], row["email"], row["country_code"], row["state_code"])

    def find_by_email(self, email: str) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
        if row is None:
            return None
        return Customer(row["id"], row["name"], row["email"], row["country_code"], row["state_code"])

    def list_all(self) -> list[Customer]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM customers").fetchall()
        return [Customer(r["id"], r["name"], r["email"], r["country_code"], r["state_code"]) for r in rows]


# ============================================================
# PLANS  +  PLAN TIERS
# ============================================================
class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan: Plan) -> Plan:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plans (name, pricing_type, billing_period, currency, config_json) VALUES (?, ?, ?, ?, ?)",
                (plan.name, plan.pricing_type.value, plan.billing_period.value, plan.currency, plan.config_json),
            )
            return Plan(cur.lastrowid, plan.name, plan.pricing_type, plan.billing_period, plan.currency, plan.config_json)

    def get(self, plan_id: int) -> Optional[Plan]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        return Plan(row["id"], row["name"], PricingType(row["pricing_type"]),
                    BillingPeriod(row["billing_period"]), row["currency"], row["config_json"])

    def list_all(self) -> list[Plan]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM plans").fetchall()
        return [Plan(r["id"], r["name"], PricingType(r["pricing_type"]),
                     BillingPeriod(r["billing_period"]), r["currency"], r["config_json"]) for r in rows]


class PlanTierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan_id: int, from_units: int, to_units: Optional[int], unit_price: Money) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plan_tiers (plan_id, from_units, to_units, unit_price) VALUES (?, ?, ?, ?)",
                (plan_id, from_units, to_units, unit_price.to_storage()),
            )
            return cur.lastrowid

    def list_for_plan(self, plan_id: int, currency: str) -> list[tuple[int, Optional[int], Money]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT from_units, to_units, unit_price FROM plan_tiers WHERE plan_id = ? ORDER BY from_units",
                (plan_id,),
            ).fetchall()
        return [(r["from_units"], r["to_units"], Money(r["unit_price"], currency)) for r in rows]


# ============================================================
# DISCOUNTS
# ============================================================
class DiscountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, code: str, discount_type: str, value: str, currency: Optional[str] = None) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO discounts (code, discount_type, value, currency) VALUES (?, ?, ?, ?)",
                (code, discount_type, value, currency),
            )
            return cur.lastrowid

    def get_by_code(self, code: str) -> Optional[dict]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM discounts WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        return dict(row)


# ============================================================
# SUBSCRIPTIONS
# ============================================================
class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription: Subscription) -> Subscription:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO subscriptions
                   (customer_id, plan_id, status, current_period_start, current_period_end,
                    trial_end, discount_id, past_due_since)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    subscription.customer_id,
                    subscription.plan_id,
                    subscription.status.value,
                    subscription.current_period_start.isoformat(),
                    subscription.current_period_end.isoformat(),
                    subscription.trial_end.isoformat() if subscription.trial_end else None,
                    subscription.discount_id,
                    subscription.past_due_since.isoformat() if subscription.past_due_since else None,
                ),
            )
            return Subscription(
                cur.lastrowid,
                subscription.customer_id,
                subscription.plan_id,
                subscription.status,
                subscription.current_period_start,
                subscription.current_period_end,
                subscription.trial_end,
                subscription.discount_id,
                subscription.past_due_since,
            )

    def get(self, subscription_id: int) -> Optional[Subscription]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_subscription(row)

    def list_all(self) -> list[Subscription]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM subscriptions").fetchall()
        return [self._row_to_subscription(r) for r in rows]

    def get_due_for_billing(self, as_of: date) -> list[Subscription]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE status = 'ACTIVE' AND current_period_end <= ?",
                (as_of.isoformat(),),
            ).fetchall()
        return [self._row_to_subscription(r) for r in rows]

    def update_period(self, subscription_id: int, new_start: date, new_end: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET current_period_start = ?, current_period_end = ? WHERE id = ?",
                (new_start.isoformat(), new_end.isoformat(), subscription_id),
            )

    def update_status(
        self,
        subscription_id: int,
        new_status: SubscriptionStatus,
        past_due_since: Optional[date] = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET status = ?, past_due_since = ? WHERE id = ?",
                (new_status.value, past_due_since.isoformat() if past_due_since else None, subscription_id),
            )

    def update_plan(self, subscription_id: int, new_plan_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                (new_plan_id, subscription_id),
            )

    def _row_to_subscription(self, row) -> Subscription:
        return Subscription(
            id=row["id"],
            customer_id=row["customer_id"],
            plan_id=row["plan_id"],
            status=SubscriptionStatus(row["status"]),
            current_period_start=date.fromisoformat(row["current_period_start"]),
            current_period_end=date.fromisoformat(row["current_period_end"]),
            trial_end=date.fromisoformat(row["trial_end"]) if row["trial_end"] else None,
            discount_id=row["discount_id"],
            past_due_since=date.fromisoformat(row["past_due_since"]) if row["past_due_since"] else None,
        )


# ============================================================
# USAGE
# ============================================================
class UsageRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription_id: int, metric: str, quantity: int) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO usage_records (subscription_id, metric, quantity) VALUES (?, ?, ?)",
                (subscription_id, metric, quantity),
            )
            return cur.lastrowid

    def sum_for_period(
        self, subscription_id: int, metric: str, period_start: date, period_end: date
    ) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(quantity), 0) as total FROM usage_records
                   WHERE subscription_id = ? AND metric = ?""",
                (subscription_id, metric),
            ).fetchone()
        return row["total"]


# ============================================================
# INVOICES + LINE ITEMS
# ============================================================
class InvoiceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, invoice: Invoice) -> Invoice:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO invoices
                   (subscription_id, period_start, period_end, currency,
                    subtotal, discount_total, tax_total, total, status, issued_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    invoice.issued_at.isoformat() if invoice.issued_at else None,
                ),
            )
            invoice.id = cur.lastrowid
            return invoice

    def get(self, invoice_id: int) -> Optional[Invoice]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_invoice(row)

    def count_for_subscription(self, subscription_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM invoices WHERE subscription_id = ?",
                (subscription_id,),
            ).fetchone()
        return row["cnt"]

    def mark_paid(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET status = 'PAID' WHERE id = ?", (invoice_id,))

    def mark_failed(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET status = 'FAILED' WHERE id = ?", (invoice_id,))

    def set_pdf_path(self, invoice_id: int, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET pdf_path = ? WHERE id = ?", (path, invoice_id))

    def _row_to_invoice(self, row) -> Invoice:
        currency = row["currency"]
        return Invoice(
            id=row["id"],
            subscription_id=row["subscription_id"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            subtotal=Money(row["subtotal"], currency),
            discount_total=Money(row["discount_total"], currency),
            tax_total=Money(row["tax_total"], currency),
            total=Money(row["total"], currency),
            status=InvoiceStatus(row["status"]),
            issued_at=datetime.fromisoformat(row["issued_at"]) if row["issued_at"] else None,
            pdf_path=row["pdf_path"],
        )


class InvoiceLineItemRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, line_item: InvoiceLineItem) -> InvoiceLineItem:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) VALUES (?, ?, ?, ?)",
                (line_item.invoice_id, line_item.description,
                 line_item.amount.to_storage(), line_item.kind.value),
            )
            return InvoiceLineItem(cur.lastrowid, line_item.invoice_id,
                                   line_item.description, line_item.amount, line_item.kind)

    def list_for_invoice(self, invoice_id: int) -> list[InvoiceLineItem]:
        with self.db.connect() as conn:
            inv_row = conn.execute("SELECT currency FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            rows = conn.execute(
                "SELECT * FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)
            ).fetchall()
        if inv_row is None:
            return []
        currency = inv_row["currency"]
        return [
            InvoiceLineItem(r["id"], r["invoice_id"], r["description"],
                            Money(r["amount"], currency), LineItemKind(r["kind"]))
            for r in rows
        ]


# ============================================================
# LEDGER — APPEND-ONLY
# ============================================================
class LedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, entry: LedgerEntry) -> LedgerEntry:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO ledger_entries (invoice_id, customer_id, amount, currency, direction, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry.invoice_id,
                    entry.customer_id,
                    entry.amount.to_storage(),
                    entry.amount.currency,
                    entry.direction.value,
                    entry.reason,
                ),
            )
            return LedgerEntry(cur.lastrowid, entry.invoice_id, entry.customer_id,
                               entry.amount, entry.direction, entry.reason)

    def list_for_customer(self, customer_id: int) -> list[LedgerEntry]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE customer_id = ? ORDER BY created_at",
                (customer_id,),
            ).fetchall()
        return [
            LedgerEntry(r["id"], r["invoice_id"], r["customer_id"],
                        Money(r["amount"], r["currency"]),
                        LedgerDirection(r["direction"]), r["reason"])
            for r in rows
        ]

    def update(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")


# ============================================================
# PAYMENT ATTEMPTS
# ============================================================
class PaymentAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        invoice_id: int,
        attempt_no: int,
        status: str,
        failure_reason: Optional[str],
        next_retry_at: Optional[datetime],
    ) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO payment_attempts
                   (invoice_id, attempt_no, status, failure_reason, next_retry_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at.isoformat() if next_retry_at else None,
                ),
            )
            return cur.lastrowid

    def list_for_invoice(self, invoice_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_attempts WHERE invoice_id = ? ORDER BY attempt_no",
                (invoice_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_for_invoice(self, invoice_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM payment_attempts WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        return row["cnt"]

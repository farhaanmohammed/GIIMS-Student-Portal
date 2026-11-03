"""Smoke tests for the Phase 5 service layer."""

from datetime import date
from decimal import Decimal

from apps.fees.selectors import installment_outstanding
from apps.payments.models import (
    AdvanceCredit,
    InstallmentAllocation,
    Payment,
    Receipt,
)
from apps.payments.selectors import available_credit
from apps.payments.services import financial_year_for, record_payment
from apps.payments.tests.factories import PaymentTestBase


class PaymentSmokeTests(PaymentTestBase):
    def test_fifo_payment_allocates_oldest_first(self):
        plan = self.make_approved_plan()
        installments = list(plan.installments.order_by("sequence"))
        payment = record_payment(
            plan.admission,
            amount=Decimal("5000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
        )
        allocations = list(payment.allocations.order_by("installment__sequence"))
        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].installment_id, installments[0].pk)
        self.assertEqual(allocations[0].amount, Decimal("1000.00"))
        self.assertEqual(allocations[1].installment_id, installments[1].pk)
        self.assertEqual(allocations[1].amount, Decimal("4000.00"))
        self.assertEqual(available_credit(plan.admission), Decimal("0.00"))
        self.assertEqual(installment_outstanding(installments[1]), Decimal("6000.00"))

    def test_receipt_number_is_centre_and_year_scoped(self):
        plan = self.make_approved_plan()
        payment = record_payment(
            plan.admission,
            amount=Decimal("1000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
        )
        self.assertEqual(payment.receipt.number, "GIIMS/TVM/FY26-27/000001")
        self.assertEqual(payment.status, Payment.Status.POSTED)
        self.assertEqual(payment.receipt.status, Receipt.Status.ACTIVE)

    def test_overpayment_creates_credit(self):
        plan = self.make_approved_plan()
        payment = record_payment(
            plan.admission,
            amount=Decimal("250000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
        )
        total = sum((row.amount for row in payment.allocations.all()), Decimal("0.00"))
        self.assertEqual(total, plan.total_payable)
        credit = self.credit_lot(payment)
        self.assertEqual(credit.remaining_amount, Decimal("250000.00") - plan.total_payable)
        self.assertEqual(credit.source_type, AdvanceCredit.Source.OVERPAYMENT)

    def test_financial_year_boundaries(self):
        self.assertEqual(financial_year_for(date(2026, 3, 31)), "FY25-26")
        self.assertEqual(financial_year_for(date(2026, 4, 1)), "FY26-27")
        self.assertEqual(financial_year_for(date(2027, 1, 20)), "FY26-27")

    def test_idempotent_request_key(self):
        plan = self.make_approved_plan()
        first = record_payment(
            plan.admission,
            amount=Decimal("1000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
            request_key="smoke-key-1",
        )
        second = record_payment(
            plan.admission,
            amount=Decimal("1000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
            request_key="smoke-key-1",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(InstallmentAllocation.objects.count(), 1)

"""Advance-credit lots: creation, FIFO application, limits, idempotency, reconciliation."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Sum

from apps.fees.selectors import admission_fee_summary, installment_outstanding
from apps.fees.services import approve_fee_plan, create_fee_plan_version
from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
)
from apps.payments.selectors import (
    available_credit,
    credit_remaining_from_history,
)
from apps.payments.services import (
    apply_advance_credit,
    decide_payment_reversal,
    decide_refund,
    record_payment,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for this financial movement."


class AdvanceCreditTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))
        self.course_one = self.installments[2]

    def make_lot(self, amount, *, admission=None):
        admission = admission or self.admission
        payment = record_payment(
            admission,
            amount=amount,
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=admission.centre,
            actor=self.accounts,
            allocations=[],
        )
        return payment, AdvanceCredit.objects.get(source_payment=payment)

    def apply_credit(self, installment, amount, key, actor=None):
        return apply_advance_credit(
            installment,
            amount=amount,
            actor=actor or self.accounts,
            request_key=key,
        )

    # -- lot creation ------------------------------------------------------
    def test_overpayment_lot_records_original_and_remaining(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))

        lot = self.credit_lot(payment)
        self.assertEqual(lot.original_amount, Decimal("52350.00"))
        self.assertEqual(lot.remaining_amount, Decimal("52350.00"))
        self.assertEqual(lot.source_type, AdvanceCredit.Source.OVERPAYMENT)
        self.assertEqual(lot.source_payment_id, payment.pk)
        self.assertEqual(lot.status, AdvanceCredit.Status.ACTIVE)
        self.assertEqual(lot.created_by, self.accounts)

    def test_payment_with_no_outstanding_creates_a_full_lot(self):
        self.make_payment(admission=self.admission, amount=self.plan.total_payable)
        payment = self.make_payment(admission=self.admission, amount=Decimal("5000.00"))

        self.assertFalse(payment.allocations.exists())
        lot = self.credit_lot(payment)
        self.assertEqual(lot.original_amount, Decimal("5000.00"))
        self.assertEqual(lot.remaining_amount, Decimal("5000.00"))

    # -- FIFO application --------------------------------------------------
    def test_credit_is_applied_oldest_lot_first(self):
        _, first_lot = self.make_lot(Decimal("10000.00"))
        _, second_lot = self.make_lot(Decimal("5000.00"))

        applied = self.apply_credit(self.course_one, Decimal("12000.00"), "credit-1")

        self.assertEqual(applied, Decimal("12000.00"))
        rows = list(
            InstallmentAllocation.objects.filter(
                installment=self.course_one, source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
            ).order_by("pk")
        )
        self.assertEqual(
            [(row.credit_id, row.amount) for row in rows],
            [
                (first_lot.pk, Decimal("10000.00")),
                (second_lot.pk, Decimal("2000.00")),
            ],
        )
        first_lot.refresh_from_db()
        second_lot.refresh_from_db()
        self.assertEqual(first_lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(first_lot.status, AdvanceCredit.Status.DEPLETED)
        self.assertEqual(second_lot.remaining_amount, Decimal("3000.00"))
        self.assertEqual(second_lot.status, AdvanceCredit.Status.ACTIVE)
        self.assertEqual(available_credit(self.admission), Decimal("3000.00"))

    def test_credit_allocation_carries_source_type_and_audit_event(self):
        _, lot = self.make_lot(Decimal("5000.00"))

        self.apply_credit(self.course_one, Decimal("5000.00"), "credit-audit")

        allocation = InstallmentAllocation.objects.get(
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
        )
        self.assertIsNone(allocation.payment_id)
        self.assertEqual(allocation.credit_id, lot.pk)
        self.assertEqual(allocation.allocated_by, self.accounts)
        self.assertEqual(allocation.request_key, "credit-audit")
        event = FinancialAuditEvent.objects.get(
            event_type=FinancialAuditEvent.Event.CREDIT_APPLIED,
            installment=self.course_one,
        )
        self.assertEqual(event.amount, Decimal("5000.00"))
        self.assertEqual(event.actor, self.accounts)

    # -- limits ------------------------------------------------------------
    def test_cannot_apply_beyond_the_installment_outstanding(self):
        _, lot = self.make_lot(Decimal("50000.00"))

        with self.assertRaisesMessage(ValueError, "exceeds the installment's outstanding"):
            self.apply_credit(self.course_one, Decimal("40000.01"), "over-outstanding")

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("50000.00"))
        self.assertFalse(
            InstallmentAllocation.objects.filter(
                source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
            ).exists()
        )

    def test_cannot_apply_beyond_the_available_credit(self):
        _, lot = self.make_lot(Decimal("5000.00"))

        with self.assertRaisesMessage(ValueError, "exceeds the available advance credit"):
            self.apply_credit(self.course_one, Decimal("5000.01"), "over-credit")

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("5000.00"))

    def test_application_to_a_fully_paid_installment_is_rejected(self):
        _, lot = self.make_lot(Decimal("5000.00"))
        self.make_payment(
            admission=self.admission,
            amount=Decimal("1000.00"),
            allocations=[{"installment": self.installments[0], "amount": Decimal("1000.00")}],
        )

        with self.assertRaisesMessage(ValueError, "no outstanding balance"):
            self.apply_credit(self.installments[0], Decimal("100.00"), "no-outstanding")

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("5000.00"))

    def test_application_to_a_superseded_plan_is_rejected(self):
        _, lot = self.make_lot(Decimal("5000.00"))
        new_version = create_fee_plan_version(self.plan, self.admin)
        approve_fee_plan(new_version, self.admin)

        with self.assertRaisesMessage(ValueError, "approved fee plan"):
            self.apply_credit(self.course_one, Decimal("1000.00"), "stale-plan")

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("5000.00"))

    def test_request_key_is_required(self):
        self.make_lot(Decimal("5000.00"))

        with self.assertRaisesMessage(ValueError, "request key is required"):
            self.apply_credit(self.course_one, Decimal("1000.00"), "")

    # -- idempotency -------------------------------------------------------
    def test_same_request_key_does_not_apply_twice(self):
        _, lot = self.make_lot(Decimal("10000.00"))

        first = self.apply_credit(self.course_one, Decimal("5000.00"), "retry-key")
        second = self.apply_credit(self.course_one, Decimal("5000.00"), "retry-key")

        self.assertEqual(first, Decimal("5000.00"))
        self.assertEqual(second, Decimal("5000.00"))
        self.assertEqual(InstallmentAllocation.objects.filter(request_key="retry-key").count(), 1)
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("5000.00"))

    def test_request_key_cannot_be_reused_for_another_installment(self):
        self.make_lot(Decimal("10000.00"))
        self.apply_credit(self.course_one, Decimal("1000.00"), "shared-key")

        with self.assertRaisesMessage(ValueError, "used for another installment"):
            self.apply_credit(self.installments[3], Decimal("1000.00"), "shared-key")

    def test_different_request_keys_still_cannot_double_apply_money(self):
        _, lot = self.make_lot(Decimal("10000.00"))
        self.apply_credit(self.course_one, Decimal("6000.00"), "first-key")

        with self.assertRaisesMessage(ValueError, "exceeds the available advance credit"):
            self.apply_credit(self.course_one, Decimal("6000.00"), "second-key")

        self.apply_credit(self.course_one, Decimal("4000.00"), "second-key")
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(lot.status, AdvanceCredit.Status.DEPLETED)
        self.assertEqual(
            InstallmentAllocation.objects.filter(
                source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
            ).aggregate(total=Sum("amount"))["total"],
            Decimal("10000.00"),
        )

    # -- balances and reconciliation --------------------------------------
    def test_available_credit_is_distinct_from_outstanding(self):
        self.make_payment(admission=self.admission, amount=Decimal("15000.00"), allocations=[])

        self.assertEqual(available_credit(self.admission), Decimal("15000.00"))
        self.assertEqual(
            sum(
                (installment_outstanding(row) for row in self.installments),
                Decimal("0.00"),
            ),
            self.plan.total_payable,
        )
        summary = admission_fee_summary(self.admission, date(2026, 7, 15))
        self.assertEqual(summary.available_credit, Decimal("15000.00"))
        self.assertEqual(summary.total_outstanding, self.plan.total_payable)

    def test_credit_remaining_from_history_reconciles_with_stored_balance(self):
        _, lot = self.make_lot(Decimal("20000.00"))
        self.apply_credit(self.course_one, Decimal("5000.00"), "reconcile-apply")
        refund = request_refund(
            lot,
            amount=Decimal("3000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="reconcile-refund",
        )
        decide_refund(refund, approve=True, actor=self.admin)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("12000.00"))
        self.assertEqual(credit_remaining_from_history(lot), Decimal("12000.00"))

    def test_reversed_lot_reconciles_to_zero(self):
        payment, lot = self.make_lot(Decimal("52350.00"))
        self.apply_credit(self.course_one, Decimal("5000.00"), "before-reversal")

        reversal = request_payment_reversal(
            payment, reason=REASON, actor=self.accounts, request_key="reverse-lot"
        )
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(lot.status, AdvanceCredit.Status.REVERSED)
        self.assertEqual(credit_remaining_from_history(lot), Decimal("0.00"))

    def test_accounts_without_centre_access_cannot_apply_credit(self):
        from apps.fees.tests.factories import make_user

        outsider = make_user("outsider_accounts", role="ACCOUNTS")
        outsider.centres.add(self.other_centre)
        self.make_lot(Decimal("5000.00"))

        with self.assertRaises(PermissionDenied):
            self.apply_credit(self.course_one, Decimal("1000.00"), "outsider", actor=outsider)

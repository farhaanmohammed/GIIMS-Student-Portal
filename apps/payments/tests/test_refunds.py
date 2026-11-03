"""Refund request/decision workflow against advance-credit lots."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    Payment,
    Receipt,
    Refund,
    RefundCreditAllocation,
)
from apps.payments.services import (
    decide_payment_reversal,
    decide_refund,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for returning this advance credit."


class RefundTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))
        self.lot = self.credit_lot(self.payment)

    def request(self, amount=Decimal("10000.00"), *, credit=None, key="refund-1", **overrides):
        kwargs = {
            "amount": amount,
            "reason": REASON,
            "mode": "BANK_TRANSFER",
            "actor": self.accounts,
            "request_key": key,
        }
        kwargs.update(overrides)
        return request_refund(credit or self.lot, **kwargs)

    # -- request path ------------------------------------------------------
    def test_accounts_can_request_a_refund(self):
        refund = self.request()

        self.assertEqual(refund.status, Refund.Status.PENDING)
        self.assertEqual(refund.requested_by, self.accounts)
        self.assertEqual(refund.admission, self.admission)
        self.assertEqual(refund.credit, self.lot)
        self.assertEqual(refund.original_payment, self.payment)
        self.assertEqual(refund.amount, Decimal("10000.00"))
        self.assertEqual(refund.reason, REASON)
        self.assertEqual(refund.mode, "BANK_TRANSFER")
        self.assertIsNone(refund.decided_by)
        self.assertIsNone(refund.refunded_on)
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REFUND_REQUESTED,
                actor=self.accounts,
            ).exists()
        )

    def test_request_does_not_reduce_credit(self):
        self.request()

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("52350.00"))
        self.assertEqual(self.lot.status, AdvanceCredit.Status.ACTIVE)

    def test_reason_shorter_than_ten_characters_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            self.request(reason="   short   ")

        self.assertFalse(Refund.objects.exists())

    def test_invalid_mode_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "valid payment mode"):
            self.request(mode="BITCOIN")

    def test_refund_cannot_exceed_the_lot_remaining(self):
        with self.assertRaisesMessage(ValueError, "exceeds the available refundable credit"):
            self.request(amount=Decimal("52350.01"))

        self.assertFalse(Refund.objects.exists())
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("52350.00"))

    def test_reversed_lot_cannot_be_refunded(self):
        reversal = request_payment_reversal(
            self.payment, reason=REASON, actor=self.accounts, request_key="rev-for-refund"
        )
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        with self.assertRaisesMessage(ValueError, "reversed payment cannot be refunded"):
            self.request()

    def test_request_key_is_idempotent(self):
        first = self.request()
        second = self.request()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REFUND_REQUESTED
            ).count(),
            1,
        )

    def test_request_key_cannot_be_reused_for_another_credit_lot(self):
        other_payment = self.make_payment(
            admission=self.admission, amount=Decimal("5000.00"), allocations=[]
        )
        other_lot = self.credit_lot(other_payment)
        first = self.request(key="shared-refund-key")
        self.assertNotEqual(first.credit_id, other_lot.pk)

        with self.assertRaisesMessage(ValueError, "belongs to another refund request"):
            self.request(credit=other_lot, amount=Decimal("1000.00"), key="shared-refund-key")

        self.assertEqual(Refund.objects.count(), 1)

    def test_admissions_and_out_of_centre_accounts_cannot_request(self):
        from apps.fees.tests.factories import make_user

        outsider = make_user("outsider_refund_accounts", role="ACCOUNTS")
        outsider.centres.add(self.other_centre)

        for actor in (self.admissions, outsider):
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                self.request(actor=actor, key=f"blocked-{actor.pk}")

    # -- decision path -----------------------------------------------------
    def test_accounts_and_admissions_cannot_decide(self):
        refund = self.request()

        for actor in (self.accounts, self.admissions):
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                decide_refund(refund, approve=True, actor=actor)

        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.PENDING)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("52350.00"))

    def test_admin_and_management_can_approve(self):
        first = self.request(amount=Decimal("10000.00"), key="approve-admin")
        approved = decide_refund(first, approve=True, actor=self.admin, note="Approved.")

        approved.refresh_from_db()
        self.assertEqual(approved.status, Refund.Status.APPROVED)
        self.assertEqual(approved.decided_by, self.admin)
        self.assertIsNotNone(approved.decided_at)
        self.assertEqual(approved.decision_note, "Approved.")
        self.assertEqual(approved.refunded_on, timezone.localdate())
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("42350.00"))
        self.assertEqual(
            RefundCreditAllocation.objects.get(refund=approved).amount,
            Decimal("10000.00"),
        )
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REFUND_APPROVED,
                actor=self.admin,
                amount=Decimal("10000.00"),
            ).exists()
        )

        second = self.request(amount=Decimal("5000.00"), key="approve-management")
        decided = decide_refund(second, approve=True, actor=self.management)
        decided.refresh_from_db()
        self.assertEqual(decided.status, Refund.Status.APPROVED)
        self.assertEqual(decided.decided_by, self.management)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("37350.00"))

    def test_approval_records_the_actual_refund_date(self):
        refund = self.request()
        decide_refund(refund, approve=True, actor=self.admin, refunded_on=date(2026, 8, 1))

        refund.refresh_from_db()
        self.assertEqual(refund.refunded_on, date(2026, 8, 1))

    def test_rejection_leaves_credit_untouched(self):
        refund = self.request()

        decided = decide_refund(
            refund, approve=False, actor=self.admin, note="Not eligible for a refund."
        )

        decided.refresh_from_db()
        self.assertEqual(decided.status, Refund.Status.REJECTED)
        self.assertEqual(decided.decided_by, self.admin)
        self.assertEqual(decided.decision_note, "Not eligible for a refund.")
        self.assertIsNone(decided.refunded_on)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("52350.00"))
        self.assertEqual(self.lot.status, AdvanceCredit.Status.ACTIVE)
        self.assertFalse(RefundCreditAllocation.objects.exists())
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REFUND_REJECTED,
                actor=self.admin,
            ).exists()
        )

    def test_full_refund_depletes_the_lot(self):
        refund = self.request(amount=Decimal("52350.00"))
        decide_refund(refund, approve=True, actor=self.admin)

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(self.lot.status, AdvanceCredit.Status.DEPLETED)

    def test_a_decided_refund_cannot_be_decided_again(self):
        refund = self.request()
        decide_refund(refund, approve=True, actor=self.admin)
        refund.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "Only pending refunds"):
            decide_refund(refund, approve=False, actor=self.admin)

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_amount, Decimal("42350.00"))

    def test_approved_refund_reduces_the_amount_available_for_the_next_one(self):
        first = self.request(amount=Decimal("30000.00"), key="first-refund")
        decide_refund(first, approve=True, actor=self.admin)

        with self.assertRaisesMessage(ValueError, "exceeds the available refundable credit"):
            self.request(amount=Decimal("30000.00"), key="second-refund")

        self.assertEqual(Refund.objects.filter(status=Refund.Status.APPROVED).count(), 1)

    def test_original_payment_and_receipt_are_never_changed(self):
        payment_before = {
            "status": self.payment.status,
            "amount": self.payment.amount,
            "request_key": self.payment.request_key,
        }
        receipt_before = {
            "number": self.payment.receipt.number,
            "status": self.payment.receipt.status,
        }

        refund = self.request()
        decide_refund(refund, approve=True, actor=self.admin)

        self.payment.refresh_from_db()
        self.payment.receipt.refresh_from_db()
        self.assertEqual(self.payment.status, payment_before["status"])
        self.assertEqual(self.payment.status, Payment.Status.POSTED)
        self.assertEqual(self.payment.amount, payment_before["amount"])
        self.assertEqual(self.payment.request_key, payment_before["request_key"])
        self.assertEqual(self.payment.receipt.number, receipt_before["number"])
        self.assertEqual(self.payment.receipt.status, Receipt.Status.ACTIVE)
        self.assertEqual(receipt_before["status"], Receipt.Status.ACTIVE)

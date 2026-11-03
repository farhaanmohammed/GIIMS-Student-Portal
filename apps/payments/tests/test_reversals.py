"""Payment reversal workflow: request, approval, rejection, and compensation."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied

from apps.fees.selectors import installment_outstanding
from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
    Payment,
    PaymentReversal,
    Receipt,
)
from apps.payments.services import (
    apply_advance_credit,
    decide_payment_reversal,
    decide_refund,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for reversing this collection."


class PaymentReversalTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))

    def make_collection(self, amount=Decimal("12000.00"), **kwargs):
        return self.make_payment(admission=self.admission, amount=amount, **kwargs)

    def request(self, payment, *, key="reversal-1", **overrides):
        kwargs = {"reason": REASON, "actor": self.accounts, "request_key": key}
        kwargs.update(overrides)
        return request_payment_reversal(payment, **kwargs)

    # -- request path ------------------------------------------------------
    def test_accounts_can_request_a_reversal(self):
        payment = self.make_collection()

        reversal = self.request(payment)

        self.assertEqual(reversal.status, PaymentReversal.Status.PENDING)
        self.assertEqual(reversal.payment, payment)
        self.assertEqual(reversal.amount, Decimal("12000.00"))
        self.assertEqual(reversal.reason, REASON)
        self.assertEqual(reversal.requested_by, self.accounts)
        self.assertIsNone(reversal.decided_by)
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REVERSAL_REQUESTED,
                actor=self.accounts,
                payment=payment,
            ).exists()
        )

    def test_admissions_cannot_request_a_reversal(self):
        payment = self.make_collection()

        with self.assertRaises(PermissionDenied):
            self.request(payment, actor=self.admissions)

        self.assertFalse(PaymentReversal.objects.exists())

    def test_reason_shorter_than_ten_characters_is_rejected(self):
        payment = self.make_collection()

        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            self.request(payment, reason="short")

        self.assertFalse(PaymentReversal.objects.exists())

    def test_duplicate_request_is_blocked(self):
        payment = self.make_collection()
        self.request(payment, key="first-key")

        with self.assertRaisesMessage(ValueError, "already exists for this payment"):
            self.request(payment, key="second-key")

        self.assertEqual(PaymentReversal.objects.count(), 1)

    def test_same_request_key_is_idempotent(self):
        payment = self.make_collection()
        first = self.request(payment, key="same-key")
        second = self.request(payment, key="same-key")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentReversal.objects.count(), 1)

    def test_request_key_cannot_be_reused_for_another_payment(self):
        first_payment = self.make_collection()
        second_payment = self.make_collection(amount=Decimal("1000.00"))
        self.request(first_payment, key="shared-reversal-key")

        with self.assertRaisesMessage(ValueError, "belongs to another payment reversal"):
            self.request(second_payment, key="shared-reversal-key")

        self.assertEqual(PaymentReversal.objects.count(), 1)

    def test_out_of_centre_accounts_cannot_request_a_reversal(self):
        from apps.fees.tests.factories import make_user

        outsider = make_user("outsider_reversal_accounts", role="ACCOUNTS")
        outsider.centres.add(self.other_centre)
        payment = self.make_collection()

        with self.assertRaises(PermissionDenied):
            self.request(payment, actor=outsider)

    def test_accounts_and_admissions_cannot_decide(self):
        payment = self.make_collection()
        reversal = self.request(payment)

        for actor in (self.accounts, self.admissions):
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                decide_payment_reversal(reversal, approve=True, actor=actor)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.POSTED)

    # -- approval ----------------------------------------------------------
    def test_approval_reverses_direct_allocations_without_deleting_them(self):
        payment = self.make_collection()
        allocation_ids = list(payment.allocations.values_list("pk", flat=True))
        original_receipt_number = payment.receipt.number
        reversal = self.request(payment)

        decided = decide_payment_reversal(
            reversal, approve=True, actor=self.admin, note="Approved by admin."
        )

        decided.refresh_from_db()
        self.assertEqual(decided.status, PaymentReversal.Status.APPROVED)
        self.assertEqual(decided.decided_by, self.admin)
        self.assertIsNotNone(decided.decided_at)
        self.assertEqual(decided.decision_note, "Approved by admin.")

        payment.refresh_from_db()
        payment.receipt.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REVERSED)
        self.assertEqual(payment.receipt.status, Receipt.Status.REVERSED)
        self.assertEqual(payment.receipt.number, original_receipt_number)

        allocations = InstallmentAllocation.objects.filter(pk__in=allocation_ids)
        self.assertEqual(allocations.count(), len(allocation_ids))
        for allocation in allocations:
            self.assertEqual(allocation.status, InstallmentAllocation.Status.REVERSED)
            self.assertEqual(allocation.reversed_by, self.admin)
            self.assertIsNotNone(allocation.reversed_at)
            self.assertIn("Payment reversal", allocation.reversal_reason)

        self.assertEqual(installment_outstanding(self.installments[0]), Decimal("1000.00"))
        self.assertEqual(installment_outstanding(self.installments[1]), Decimal("10000.00"))
        self.assertEqual(installment_outstanding(self.installments[2]), Decimal("40000.00"))
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REVERSAL_APPROVED,
                actor=self.admin,
                payment=payment,
            ).exists()
        )

    def test_approval_reverses_credit_funded_allocations_and_reverses_the_lot(self):
        payment = self.make_payment(
            admission=self.admission,
            amount=Decimal("200000.00"),
            allocations=[
                {"installment": self.installments[0], "amount": Decimal("1000.00")},
                {"installment": self.installments[1], "amount": Decimal("10000.00")},
                {"installment": self.installments[2], "amount": Decimal("40000.00")},
                {"installment": self.installments[3], "amount": Decimal("50000.00")},
            ],
        )
        lot = self.credit_lot(payment)
        self.assertEqual(lot.remaining_amount, Decimal("99000.00"))
        apply_advance_credit(
            self.installments[4],
            amount=Decimal("5000.00"),
            actor=self.accounts,
            request_key="credit-before-reversal",
        )
        credit_allocation = InstallmentAllocation.objects.get(
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
        )

        reversal = self.request(payment)
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        credit_allocation.refresh_from_db()
        self.assertEqual(credit_allocation.status, InstallmentAllocation.Status.REVERSED)
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(lot.status, AdvanceCredit.Status.REVERSED)
        for allocation in payment.allocations.all():
            self.assertEqual(allocation.status, InstallmentAllocation.Status.REVERSED)
        self.assertEqual(installment_outstanding(self.installments[3]), Decimal("50000.00"))
        self.assertEqual(installment_outstanding(self.installments[4]), Decimal("50000.00"))

    def test_nothing_is_deleted(self):
        payment = self.make_collection()
        counts = {
            "payments": Payment.objects.count(),
            "receipts": Receipt.objects.count(),
            "allocations": InstallmentAllocation.objects.count(),
            "credits": AdvanceCredit.objects.count(),
        }

        reversal = self.request(payment)
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        self.assertEqual(Payment.objects.count(), counts["payments"])
        self.assertEqual(Receipt.objects.count(), counts["receipts"])
        self.assertEqual(InstallmentAllocation.objects.count(), counts["allocations"])
        self.assertEqual(AdvanceCredit.objects.count(), counts["credits"])
        self.assertTrue(PaymentReversal.objects.filter(payment=payment).exists())

    def test_only_the_target_payment_is_reversed(self):
        first = self.make_collection()
        second = self.make_collection(amount=Decimal("5000.00"))
        second_allocation_ids = list(second.allocations.values_list("pk", flat=True))

        reversal = self.request(first)
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        second.refresh_from_db()
        self.assertEqual(second.status, Payment.Status.POSTED)
        for allocation in InstallmentAllocation.objects.filter(pk__in=second_allocation_ids):
            self.assertEqual(allocation.status, InstallmentAllocation.Status.ACTIVE)
        self.assertEqual(installment_outstanding(self.installments[0]), Decimal("1000.00"))
        self.assertEqual(installment_outstanding(self.installments[1]), Decimal("10000.00"))
        self.assertEqual(installment_outstanding(self.installments[2]), Decimal("35000.00"))

    def test_a_decided_reversal_cannot_be_decided_again(self):
        payment = self.make_collection()
        reversal = self.request(payment)
        decide_payment_reversal(reversal, approve=True, actor=self.admin)
        reversal.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "Only pending reversals"):
            decide_payment_reversal(reversal, approve=False, actor=self.admin)

    def test_a_reversed_payment_cannot_be_reversed_again(self):
        payment = self.make_collection()
        reversal = self.request(payment)
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        with self.assertRaisesMessage(ValueError, "Only a posted payment"):
            self.request(payment, key="another-reversal")

    # -- blocks and rejection ---------------------------------------------
    def test_reversal_is_blocked_while_a_refund_is_pending(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))
        lot = self.credit_lot(payment)
        request_refund(
            lot,
            amount=Decimal("10000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="pending-refund",
        )

        with self.assertRaisesMessage(ValueError, "refunds against its credit"):
            self.request(payment)

        self.assertFalse(PaymentReversal.objects.exists())

    def test_reversal_is_blocked_while_a_refund_is_approved(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))
        lot = self.credit_lot(payment)
        refund = request_refund(
            lot,
            amount=Decimal("10000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="approved-refund",
        )
        decide_refund(refund, approve=True, actor=self.admin)

        with self.assertRaisesMessage(ValueError, "refunds against its credit"):
            self.request(payment)

    def test_rejection_leaves_everything_unchanged(self):
        payment = self.make_collection()
        receipt_number = payment.receipt.number
        allocation_ids = list(payment.allocations.values_list("pk", flat=True))
        reversal = self.request(payment)

        decided = decide_payment_reversal(
            reversal, approve=False, actor=self.admin, note="Collected correctly."
        )

        decided.refresh_from_db()
        self.assertEqual(decided.status, PaymentReversal.Status.REJECTED)
        self.assertEqual(decided.decision_note, "Collected correctly.")
        payment.refresh_from_db()
        payment.receipt.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.POSTED)
        self.assertEqual(payment.receipt.status, Receipt.Status.ACTIVE)
        self.assertEqual(payment.receipt.number, receipt_number)
        for allocation in InstallmentAllocation.objects.filter(pk__in=allocation_ids):
            self.assertEqual(allocation.status, InstallmentAllocation.Status.ACTIVE)
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REVERSAL_REJECTED,
                actor=self.admin,
            ).exists()
        )

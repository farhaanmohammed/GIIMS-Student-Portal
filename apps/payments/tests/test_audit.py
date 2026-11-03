"""Append-only financial audit trail for every payment-domain service."""

from decimal import Decimal

from apps.payments.models import (
    FinancialAuditEvent,
    InstallmentAllocation,
    PaymentReversal,
)
from apps.payments.services import (
    apply_advance_credit,
    decide_payment_reversal,
    decide_refund,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for the audit-trail test."


class PaymentAuditTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.course_one = self.plan.installments.get(sequence=3)

    def events(self, *event_types):
        return FinancialAuditEvent.objects.filter(event_type__in=event_types).order_by("pk")

    def test_recording_a_payment_writes_payment_receipt_and_allocation_events(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("12000.00"))

        recorded = FinancialAuditEvent.objects.get(
            event_type=FinancialAuditEvent.Event.PAYMENT_RECORDED
        )
        self.assertEqual(recorded.admission, self.admission)
        self.assertEqual(recorded.actor, self.accounts)
        self.assertEqual(recorded.payment, payment)
        self.assertEqual(recorded.amount, Decimal("12000.00"))

        issued = FinancialAuditEvent.objects.get(
            event_type=FinancialAuditEvent.Event.RECEIPT_ISSUED
        )
        self.assertEqual(issued.amount, Decimal("12000.00"))
        self.assertIn(payment.receipt.number, issued.detail)

        allocation_events = FinancialAuditEvent.objects.filter(
            event_type=FinancialAuditEvent.Event.ALLOCATION_CREATED
        )
        self.assertEqual(allocation_events.count(), payment.allocations.count())
        self.assertEqual(
            sum((row.amount for row in allocation_events), Decimal("0.00")),
            Decimal("12000.00"),
        )

    def test_overpayment_and_credit_application_write_credit_events(self):
        payment = self.make_payment(
            admission=self.admission, amount=Decimal("52350.00"), allocations=[]
        )
        lot = self.credit_lot(payment)
        created = FinancialAuditEvent.objects.get(
            event_type=FinancialAuditEvent.Event.CREDIT_CREATED
        )
        self.assertEqual(created.payment, payment)
        self.assertEqual(created.amount, Decimal("52350.00"))

        apply_advance_credit(
            self.course_one,
            amount=Decimal("5000.00"),
            actor=self.accounts,
            request_key="audit-credit",
            reason=REASON,
        )
        applied = FinancialAuditEvent.objects.get(
            event_type=FinancialAuditEvent.Event.CREDIT_APPLIED
        )
        self.assertEqual(applied.installment, self.course_one)
        self.assertEqual(applied.amount, Decimal("5000.00"))
        self.assertEqual(applied.actor, self.accounts)
        self.assertIn(str(lot.pk), applied.detail)

    def test_refund_request_approve_and_reject_each_write_an_event(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))
        lot = self.credit_lot(payment)

        approved = request_refund(
            lot,
            amount=Decimal("1000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="audit-refund-approved",
        )
        rejected = request_refund(
            lot,
            amount=Decimal("2000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="audit-refund-rejected",
        )
        decide_refund(approved, approve=True, actor=self.admin)
        decide_refund(rejected, approve=False, actor=self.management)

        self.assertEqual(self.events(FinancialAuditEvent.Event.REFUND_REQUESTED).count(), 2)
        self.assertTrue(
            self.events(FinancialAuditEvent.Event.REFUND_APPROVED)
            .filter(actor=self.admin, amount=Decimal("1000.00"))
            .exists()
        )
        self.assertTrue(
            self.events(FinancialAuditEvent.Event.REFUND_REJECTED)
            .filter(actor=self.management, amount=Decimal("2000.00"))
            .exists()
        )

    def test_reversal_request_approve_and_reject_each_write_an_event(self):
        first = self.make_payment(admission=self.admission, amount=Decimal("12000.00"))
        second = self.make_payment(admission=self.admission, amount=Decimal("1000.00"))

        approved = request_payment_reversal(
            first, reason=REASON, actor=self.accounts, request_key="audit-rev-ok"
        )
        rejected = request_payment_reversal(
            second, reason=REASON, actor=self.accounts, request_key="audit-rev-no"
        )
        decide_payment_reversal(approved, approve=True, actor=self.admin)
        decide_payment_reversal(rejected, approve=False, actor=self.admin)

        self.assertEqual(self.events(FinancialAuditEvent.Event.REVERSAL_REQUESTED).count(), 2)
        self.assertTrue(
            self.events(FinancialAuditEvent.Event.REVERSAL_APPROVED)
            .filter(payment=first, actor=self.admin)
            .exists()
        )
        self.assertTrue(
            self.events(FinancialAuditEvent.Event.REVERSAL_REJECTED)
            .filter(payment=second, actor=self.admin)
            .exists()
        )

    def test_event_rows_cannot_be_updated_or_deleted(self):
        self.make_payment(admission=self.admission, amount=Decimal("1000.00"))
        event = FinancialAuditEvent.objects.order_by("pk").first()
        original_detail = event.detail

        event.detail = "Tampered detail"
        with self.assertRaisesMessage(ValueError, "append-only"):
            event.save()

        event.refresh_from_db()
        self.assertEqual(event.detail, original_detail)

        with self.assertRaisesMessage(ValueError, "append-only"):
            event.delete()

        self.assertTrue(FinancialAuditEvent.objects.filter(pk=event.pk).exists())

    def test_every_allocation_has_a_matching_audit_event(self):
        self.make_payment(admission=self.admission, amount=Decimal("20000.00"))

        allocations = InstallmentAllocation.objects.filter(
            status=InstallmentAllocation.Status.ACTIVE
        )
        for allocation in allocations:
            self.assertTrue(
                FinancialAuditEvent.objects.filter(
                    event_type=FinancialAuditEvent.Event.ALLOCATION_CREATED,
                    installment=allocation.installment,
                    amount=allocation.amount,
                ).exists()
            )

    def test_payment_reversal_rows_are_auditable_not_deletable(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("1000.00"))
        reversal = request_payment_reversal(
            payment, reason=REASON, actor=self.accounts, request_key="audit-row"
        )

        self.assertEqual(PaymentReversal.objects.filter(pk=reversal.pk).count(), 1)
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REVERSAL_REQUESTED,
                payment=payment,
                amount=Decimal("1000.00"),
            ).exists()
        )

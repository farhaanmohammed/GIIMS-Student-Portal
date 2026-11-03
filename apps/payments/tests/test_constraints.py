"""Database constraints for the payment domain.

These tests assert that the PostgreSQL schema protects invariants even if a
caller bypasses the services.
"""

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.models import (
    AdvanceCredit,
    InstallmentAllocation,
    Payment,
    PaymentReversal,
    Receipt,
    Refund,
    RefundCreditAllocation,
)
from apps.payments.services import request_refund
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for a database-constraint test."
TODAY = date(2026, 7, 15)


class PaymentConstraintTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installment = self.plan.installments.get(sequence=1)
        self.payment = self.make_payment(
            admission=self.admission, amount=Decimal("1000.00"), allocations=[]
        )
        self.other_payment = self.direct_payment()

    def direct_payment(self, **overrides):
        data = {
            "centre": self.centre,
            "admission": self.admission,
            "amount": Decimal("10.00"),
            "paid_on": TODAY,
            "mode": "CASH",
            "recorded_by": self.accounts,
        }
        data.update(overrides)
        return Payment.objects.create(**data)

    def test_payment_amount_must_be_positive(self):
        for amount in (Decimal("0.00"), Decimal("-1.00")):
            with (
                self.subTest(amount=amount),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                self.direct_payment(amount=amount)

    def test_receipt_sequence_must_be_positive(self):
        duplicate_payment = self.direct_payment()

        with self.assertRaises(IntegrityError), transaction.atomic():
            Receipt.objects.create(
                payment=duplicate_payment,
                centre=self.centre,
                financial_year="FY26-27",
                sequence=0,
                number="GIIMS/TVM/FY26-27/000000",
                issued_by=self.accounts,
            )

    def test_credit_remaining_cannot_be_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=self.other_payment,
                source_type=AdvanceCredit.Source.OVERPAYMENT,
                original_amount=Decimal("100.00"),
                remaining_amount=Decimal("-0.01"),
                created_by=self.accounts,
            )

    def test_credit_remaining_cannot_exceed_original(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=self.other_payment,
                source_type=AdvanceCredit.Source.OVERPAYMENT,
                original_amount=Decimal("100.00"),
                remaining_amount=Decimal("100.01"),
                created_by=self.accounts,
            )

    def test_credit_original_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=self.other_payment,
                source_type=AdvanceCredit.Source.OVERPAYMENT,
                original_amount=Decimal("0.00"),
                remaining_amount=Decimal("0.00"),
                created_by=self.accounts,
            )

    def test_overpayment_credit_requires_a_source_payment(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=None,
                source_type=AdvanceCredit.Source.OVERPAYMENT,
                original_amount=Decimal("100.00"),
                remaining_amount=Decimal("100.00"),
                created_by=self.accounts,
            )

    def test_reassignment_credit_must_not_have_a_source_payment(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=self.other_payment,
                source_type=AdvanceCredit.Source.REASSIGNMENT,
                original_amount=Decimal("100.00"),
                remaining_amount=Decimal("100.00"),
                created_by=self.accounts,
            )

    def test_one_credit_lot_per_source_payment(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdvanceCredit.objects.create(
                admission=self.admission,
                source_payment=self.payment,
                source_type=AdvanceCredit.Source.OVERPAYMENT,
                original_amount=Decimal("5.00"),
                remaining_amount=Decimal("5.00"),
                created_by=self.accounts,
            )

    def test_allocation_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("0.00"),
                source_type=InstallmentAllocation.Source.PAYMENT,
                payment=self.payment,
                allocated_by=self.accounts,
            )

    def test_payment_allocation_cannot_point_at_a_credit_lot(self):
        lot = self.credit_lot(self.payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("1.00"),
                source_type=InstallmentAllocation.Source.PAYMENT,
                payment=self.payment,
                credit=lot,
                allocated_by=self.accounts,
            )

    def test_credit_allocation_cannot_point_at_a_payment(self):
        lot = self.credit_lot(self.payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("1.00"),
                source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
                payment=self.payment,
                credit=lot,
                allocated_by=self.accounts,
            )

    def test_active_allocation_cannot_carry_reversal_metadata(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("1.00"),
                source_type=InstallmentAllocation.Source.PAYMENT,
                payment=self.payment,
                status=InstallmentAllocation.Status.ACTIVE,
                reversed_by=self.admin,
                reversed_at=timezone.now(),
                allocated_by=self.accounts,
            )

    def test_reversed_allocation_requires_actor_and_timestamp(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("1.00"),
                source_type=InstallmentAllocation.Source.PAYMENT,
                payment=self.payment,
                status=InstallmentAllocation.Status.REVERSED,
                allocated_by=self.accounts,
            )

    def test_credit_allocation_request_key_is_unique_per_lot(self):
        lot = self.credit_lot(self.payment)
        InstallmentAllocation.objects.create(
            installment=self.installment,
            amount=Decimal("1.00"),
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
            credit=lot,
            request_key="duplicate-key",
            allocated_by=self.accounts,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            InstallmentAllocation.objects.create(
                installment=self.installment,
                amount=Decimal("1.00"),
                source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
                credit=lot,
                request_key="duplicate-key",
                allocated_by=self.accounts,
            )

    def test_refund_amount_must_be_positive(self):
        lot = self.credit_lot(self.payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Refund.objects.create(
                admission=self.admission,
                credit=lot,
                amount=Decimal("0.00"),
                reason=REASON,
                mode="CASH",
                requested_by=self.accounts,
                request_key="constraint-refund",
            )

    def test_pending_refund_cannot_carry_a_decision(self):
        lot = self.credit_lot(self.payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Refund.objects.create(
                admission=self.admission,
                credit=lot,
                amount=Decimal("1.00"),
                reason=REASON,
                mode="CASH",
                requested_by=self.accounts,
                decided_by=self.admin,
                decided_at=timezone.now(),
                request_key="constraint-pending-refund",
            )

    def test_approved_refund_requires_a_refund_date(self):
        lot = self.credit_lot(self.payment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Refund.objects.create(
                admission=self.admission,
                credit=lot,
                amount=Decimal("1.00"),
                reason=REASON,
                mode="CASH",
                requested_by=self.accounts,
                status=Refund.Status.APPROVED,
                decided_by=self.admin,
                decided_at=timezone.now(),
                refunded_on=None,
                request_key="constraint-approved-refund",
            )

    def test_refund_credit_allocation_is_unique_per_lot(self):
        lot = self.credit_lot(self.payment)
        refund = request_refund(
            lot,
            amount=Decimal("1.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="constraint-refund-credit",
        )
        RefundCreditAllocation.objects.create(refund=refund, credit=lot, amount=Decimal("1.00"))

        with self.assertRaises(IntegrityError), transaction.atomic():
            RefundCreditAllocation.objects.create(refund=refund, credit=lot, amount=Decimal("0.50"))

    def test_reversal_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentReversal.objects.create(
                payment=self.payment,
                amount=Decimal("0.00"),
                reason=REASON,
                requested_by=self.accounts,
                request_key="constraint-reversal",
            )

    def test_pending_reversal_cannot_carry_a_decision(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentReversal.objects.create(
                payment=self.payment,
                amount=Decimal("1000.00"),
                reason=REASON,
                requested_by=self.accounts,
                decided_by=self.admin,
                decided_at=timezone.now(),
                request_key="constraint-pending-reversal",
            )

    def test_one_reversal_row_per_payment(self):
        PaymentReversal.objects.create(
            payment=self.payment,
            amount=Decimal("1000.00"),
            reason=REASON,
            requested_by=self.accounts,
            request_key="constraint-reversal-one",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentReversal.objects.create(
                payment=self.payment,
                amount=Decimal("1000.00"),
                reason=REASON,
                requested_by=self.accounts,
                request_key="constraint-reversal-two",
            )

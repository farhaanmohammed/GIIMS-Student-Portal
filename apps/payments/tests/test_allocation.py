"""Allocation policy tests: FIFO, staff-selected, partial, multi-installment.

These tests exercise ``record_payment`` because allocations are always created
inside a collection; advance-credit applications are covered in
``test_credit.py``.
"""

from decimal import Decimal

from apps.fees.selectors import installment_outstanding
from apps.payments.models import (
    AdvanceCredit,
    InstallmentAllocation,
    Payment,
    Receipt,
    ReceiptSequence,
)
from apps.payments.selectors import available_credit
from apps.payments.tests.factories import PaymentTestBase


class AllocationTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))
        self.application = self.installments[0]
        self.admission_fee = self.installments[1]
        self.course_one = self.installments[2]
        self.course_two = self.installments[3]

    def allocations(self, payment):
        return list(payment.allocations.order_by("installment__sequence", "pk"))

    def record(self, amount, allocations="fifo", **overrides):
        return self.make_payment(
            admission=self.admission,
            amount=amount,
            allocations=allocations,
            **overrides,
        )

    # -- FIFO --------------------------------------------------------------
    def test_fifo_default_allocates_oldest_installments_first(self):
        payment = self.record(Decimal("12000.00"))

        rows = self.allocations(payment)
        self.assertEqual(
            [(row.installment_id, row.amount) for row in rows],
            [
                (self.application.pk, Decimal("1000.00")),
                (self.admission_fee.pk, Decimal("10000.00")),
                (self.course_one.pk, Decimal("1000.00")),
            ],
        )
        self.assertEqual(available_credit(self.admission), Decimal("0.00"))

    def test_fifo_payment_equal_to_first_two_installments_leaves_no_credit(self):
        payment = self.record(Decimal("11000.00"))

        self.assertEqual(
            [(row.installment_id, row.amount) for row in self.allocations(payment)],
            [
                (self.application.pk, Decimal("1000.00")),
                (self.admission_fee.pk, Decimal("10000.00")),
            ],
        )
        self.assertEqual(available_credit(self.admission), Decimal("0.00"))

    # -- staff selection ---------------------------------------------------
    def test_staff_selected_installment_is_honoured_exactly(self):
        payment = self.record(
            Decimal("5000.00"),
            allocations=[{"installment": self.course_two, "amount": Decimal("5000.00")}],
        )

        rows = self.allocations(payment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].installment_id, self.course_two.pk)
        self.assertEqual(rows[0].amount, Decimal("5000.00"))
        # Earlier installments are untouched; a later one was chosen.
        self.assertEqual(installment_outstanding(self.application), Decimal("1000.00"))
        self.assertEqual(installment_outstanding(self.course_two), Decimal("45000.00"))

    def test_partial_payment_reduces_one_installment(self):
        self.record(
            Decimal("500.00"),
            allocations=[{"installment": self.application, "amount": Decimal("500.00")}],
        )

        self.assertEqual(installment_outstanding(self.application), Decimal("500.00"))
        self.assertEqual(installment_outstanding(self.admission_fee), Decimal("10000.00"))

    def test_one_payment_can_span_multiple_installments(self):
        payment = self.record(
            Decimal("20000.00"),
            allocations=[
                {"installment": self.course_one, "amount": Decimal("12000.00")},
                {"installment": self.course_two, "amount": Decimal("8000.00")},
            ],
        )

        rows = self.allocations(payment)
        self.assertEqual(
            [(row.installment_id, row.amount) for row in rows],
            [
                (self.course_one.pk, Decimal("12000.00")),
                (self.course_two.pk, Decimal("8000.00")),
            ],
        )
        self.assertEqual(installment_outstanding(self.course_one), Decimal("28000.00"))
        self.assertEqual(installment_outstanding(self.course_two), Decimal("42000.00"))

    def test_unallocated_remainder_is_retained_as_credit(self):
        payment = self.record(
            Decimal("12000.00"),
            allocations=[{"installment": self.application, "amount": Decimal("1000.00")}],
        )

        self.assertEqual(len(self.allocations(payment)), 1)
        credit = self.credit_lot(payment)
        self.assertEqual(credit.original_amount, Decimal("11000.00"))
        self.assertEqual(credit.remaining_amount, Decimal("11000.00"))
        self.assertEqual(credit.source_type, AdvanceCredit.Source.OVERPAYMENT)

    # -- limits ------------------------------------------------------------
    def test_allocation_above_an_installment_outstanding_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "exceeds the outstanding balance"):
            self.record(
                Decimal("5000.00"),
                allocations=[{"installment": self.application, "amount": Decimal("1000.01")}],
            )

        # The whole collection rolls back: no orphan payment, receipt or
        # allocation is left behind.
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Receipt.objects.count(), 0)
        self.assertEqual(InstallmentAllocation.objects.count(), 0)
        self.assertEqual(AdvanceCredit.objects.count(), 0)
        self.assertEqual(ReceiptSequence.objects.count(), 0)

    def test_allocation_total_above_the_payment_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "cannot exceed the payment amount"):
            self.record(
                Decimal("5000.00"),
                allocations=[
                    {"installment": self.course_one, "amount": Decimal("4000.00")},
                    {"installment": self.course_two, "amount": Decimal("2000.00")},
                ],
            )

        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InstallmentAllocation.objects.count(), 0)

    def test_repeated_rows_for_one_installment_cannot_combine_over_outstanding(self):
        # Each row is individually below the 40,000 outstanding, but together
        # they would over-allocate the same installment.
        with self.assertRaisesMessage(ValueError, "exceeds the outstanding balance"):
            self.record(
                Decimal("60000.00"),
                allocations=[
                    {"installment": self.course_one, "amount": Decimal("30000.00")},
                    {"installment": self.course_one, "amount": Decimal("30000.00")},
                ],
            )

        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(InstallmentAllocation.objects.count(), 0)

    def test_installment_from_another_plan_is_rejected(self):
        other_plan = self.make_approved_plan()
        foreign_installment = other_plan.installments.get(sequence=3)

        with self.assertRaisesMessage(ValueError, "does not belong to the approved fee plan"):
            self.record(
                Decimal("1000.00"),
                allocations=[{"installment": foreign_installment, "amount": Decimal("1000.00")}],
            )

    def test_fully_paid_installment_cannot_be_allocated_again(self):
        self.record(
            Decimal("1000.00"),
            allocations=[{"installment": self.application, "amount": Decimal("1000.00")}],
        )

        with self.assertRaisesMessage(ValueError, "exceeds the outstanding balance"):
            self.record(
                Decimal("1000.00"),
                allocations=[{"installment": self.application, "amount": Decimal("1000.00")}],
            )

    def test_negative_allocation_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "cannot be negative"):
            self.record(
                Decimal("1000.00"),
                allocations=[{"installment": self.application, "amount": Decimal("-1.00")}],
            )

    # -- overpayment and row shape ----------------------------------------
    def test_overpayment_never_allocates_beyond_the_plan(self):
        payment = self.record(Decimal("250000.00"))

        allocated = sum((row.amount for row in payment.allocations.all()), Decimal("0.00"))
        self.assertEqual(allocated, self.plan.total_payable)
        self.assertEqual(self.credit_lot(payment).remaining_amount, Decimal("52350.00"))
        for installment in self.installments:
            self.assertEqual(installment_outstanding(installment), Decimal("0.00"))

    def test_empty_allocation_list_retains_the_whole_amount_as_credit(self):
        payment = self.record(Decimal("15000.00"), allocations=[])

        self.assertFalse(payment.allocations.exists())
        credit = self.credit_lot(payment)
        self.assertEqual(credit.original_amount, Decimal("15000.00"))
        self.assertEqual(credit.remaining_amount, Decimal("15000.00"))
        self.assertEqual(credit.source_payment_id, payment.pk)

    def test_allocations_are_rows_separate_from_the_payment(self):
        payment = self.record(
            Decimal("20000.00"),
            allocations=[
                {"installment": self.course_one, "amount": Decimal("10000.00")},
                {"installment": self.course_two, "amount": Decimal("10000.00")},
            ],
        )

        rows = InstallmentAllocation.objects.filter(payment=payment)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual(row.source_type, InstallmentAllocation.Source.PAYMENT)
            self.assertIsNone(row.credit_id)
            self.assertEqual(row.status, InstallmentAllocation.Status.ACTIVE)
            self.assertEqual(row.allocated_by, self.accounts)
            self.assertIsNotNone(row.allocated_at)
        self.assertEqual(payment.amount, Decimal("20000.00"))

    def test_zero_amount_allocation_rows_are_ignored(self):
        payment = self.record(
            Decimal("1000.00"),
            allocations=[{"installment": self.application, "amount": Decimal("0.00")}],
        )

        self.assertFalse(payment.allocations.exists())
        self.assertEqual(self.credit_lot(payment).remaining_amount, Decimal("1000.00"))

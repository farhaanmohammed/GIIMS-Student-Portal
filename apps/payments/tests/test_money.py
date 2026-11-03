"""Decimal accuracy: quantization, smallest units, and exact sums."""

from decimal import Decimal

from apps.fees.selectors import installment_outstanding
from apps.payments.models import InstallmentAllocation
from apps.payments.selectors import available_credit, credit_remaining_from_history
from apps.payments.services import (
    apply_advance_credit,
    decide_refund,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for a precision test refund."


class MoneyPrecisionTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))

    def record(self, amount, allocations="fifo", **overrides):
        return self.make_payment(
            admission=self.admission,
            amount=amount,
            allocations=allocations,
            **overrides,
        )

    def test_amount_is_quantized_half_up_to_two_places(self):
        rounded_up = self.record(Decimal("100.005"))
        self.assertEqual(rounded_up.amount, Decimal("100.01"))
        self.assertIsInstance(rounded_up.amount, Decimal)

        rounded_down = self.record(Decimal("100.004"))
        self.assertEqual(rounded_down.amount, Decimal("100.00"))

    def test_decimal_string_input_is_accepted(self):
        payment = self.record("1234.5")

        self.assertEqual(payment.amount, Decimal("1234.50"))

    def test_float_input_does_not_introduce_binary_artifacts(self):
        payment = self.record(0.1 + 0.2)

        self.assertEqual(payment.amount, Decimal("0.30"))
        self.assertEqual(str(payment.amount), "0.30", "No binary floating point artifact may leak.")

    def test_smallest_unit_allocations_sum_exactly(self):
        payment = self.record(
            Decimal("0.03"),
            allocations=[
                {"installment": self.installments[0], "amount": Decimal("0.01")},
                {"installment": self.installments[1], "amount": Decimal("0.01")},
                {"installment": self.installments[2], "amount": Decimal("0.01")},
            ],
        )

        amounts = [row.amount for row in payment.allocations.order_by("installment__sequence")]
        self.assertEqual(amounts, [Decimal("0.01")] * 3)
        self.assertEqual(sum(amounts, Decimal("0.00")), Decimal("0.03"))
        self.assertEqual(available_credit(self.admission), Decimal("0.00"))

    def test_outstanding_drops_by_exactly_the_smallest_unit(self):
        self.record(
            Decimal("0.01"),
            allocations=[{"installment": self.installments[0], "amount": Decimal("0.01")}],
        )

        self.assertEqual(installment_outstanding(self.installments[0]), Decimal("999.99"))

    def test_allocations_plus_credit_always_equal_the_payment_amount(self):
        payment = self.record(
            Decimal("12345.67"),
            allocations=[
                {"installment": self.installments[0], "amount": Decimal("1000.00")},
                {"installment": self.installments[1], "amount": Decimal("2345.67")},
            ],
        )

        allocated = sum((row.amount for row in payment.allocations.all()), Decimal("0.00"))
        credit = available_credit(self.admission)
        self.assertEqual(allocated, Decimal("3345.67"))
        self.assertEqual(credit, Decimal("9000.00"))
        self.assertEqual(allocated + credit, payment.amount)

    def test_credit_application_and_refund_decrements_are_exact(self):
        payment = self.record(Decimal("0.05"), allocations=[])
        lot = self.credit_lot(payment)
        self.assertEqual(lot.remaining_amount, Decimal("0.05"))

        apply_advance_credit(
            self.installments[0],
            amount=Decimal("0.01"),
            actor=self.accounts,
            request_key="precision-apply",
        )
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.04"))

        refund = request_refund(
            lot,
            amount=Decimal("0.02"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="precision-refund",
        )
        decide_refund(refund, approve=True, actor=self.admin)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.02"))
        self.assertEqual(credit_remaining_from_history(lot), Decimal("0.02"))
        self.assertEqual(available_credit(self.admission), Decimal("0.02"))

    def test_every_allocation_keeps_two_decimal_places(self):
        self.record(
            Decimal("100.10"),
            allocations=[{"installment": self.installments[0], "amount": Decimal("100.10")}],
        )

        allocation = InstallmentAllocation.objects.get(payment__amount=Decimal("100.10"))
        self.assertEqual(allocation.amount, Decimal("100.10"))
        self.assertEqual(str(allocation.amount), "100.10")

"""Form validation for collection, credit, refund and decision forms."""

from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.payments.forms import (
    CreditApplicationForm,
    DecisionForm,
    PaymentForm,
    PaymentReversalForm,
    RefundDecisionForm,
    RefundForm,
)
from apps.payments.services import apply_advance_credit, decide_refund, request_refund
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for the form-validation test."


class PaymentFormTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))
        self.application = self.installments[0]

    def data(self, **overrides):
        data = {
            "collection_centre": self.centre.pk,
            "amount": "50000.00",
            "paid_on": "2026-07-15",
            "mode": "CASH",
            "reference": "",
            "notes": "",
            "request_key": "form-request",
        }
        data.update(overrides)
        return data

    def form(self, **overrides):
        return PaymentForm(self.data(**overrides), admission=self.admission, user=self.accounts)

    def test_blank_allocations_mean_fifo(self):
        form = self.form()

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.allocations_argument())
        self.assertEqual(form.cleaned_data["amount"], Decimal("50000.00"))

    def test_every_installment_gets_an_allocation_field(self):
        form = self.form()

        self.assertEqual(len(form.installments), 6)
        self.assertEqual(len(form.allocation_fields()), 6)
        for installment in self.installments:
            self.assertIn(form.allocation_field_name(installment), form.fields)
        self.assertEqual(
            form.allocation_field_name(self.installments[0]), f"allocation_{self.application.pk}"
        )

    def test_explicit_allocation_is_returned_as_a_row(self):
        form = self.form(**{f"allocation_{self.application.pk}": "1000.00"})

        self.assertTrue(form.is_valid(), form.errors)
        rows = form.allocation_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["installment"], self.application)
        self.assertEqual(rows[0]["amount"], Decimal("1000.00"))
        self.assertEqual(form.allocations_argument(), rows)

    def test_allocations_above_the_amount_are_rejected(self):
        form = self.form(amount="500.00", **{f"allocation_{self.application.pk}": "1000.00"})

        self.assertFalse(form.is_valid())
        self.assertIn("Allocations cannot exceed the amount received.", form.non_field_errors())

    def test_retain_as_credit_with_allocations_is_rejected(self):
        form = self.form(retain_as_credit="on", **{f"allocation_{self.application.pk}": "100.00"})

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Clear the allocation amounts",
            " ".join(form.non_field_errors()),
        )

    def test_retain_as_credit_alone_keeps_the_whole_amount(self):
        form = self.form(retain_as_credit="on")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.allocations_argument(), [])

    def test_allocation_above_outstanding_is_rejected(self):
        self.make_payment(
            admission=self.admission,
            amount=Decimal("1000.00"),
            allocations=[{"installment": self.application, "amount": Decimal("1000.00")}],
        )
        form = self.form(**{f"allocation_{self.application.pk}": "1.00"})

        self.assertFalse(form.is_valid())
        self.assertIn("only has", " ".join(form.non_field_errors()))

    def test_collection_centre_is_limited_to_accessible_centres(self):
        form = self.form()

        self.assertEqual(list(form.fields["collection_centre"].queryset), [self.centre])

        invalid = self.form(collection_centre=self.other_centre.pk)
        self.assertFalse(invalid.is_valid())
        self.assertIn("collection_centre", invalid.errors)

    def test_paid_on_defaults_to_today(self):
        form = PaymentForm(admission=self.admission, user=self.accounts)

        self.assertEqual(form.initial["paid_on"], timezone.localdate())

    def test_amount_below_the_smallest_unit_is_rejected(self):
        form = self.form(amount="0.00")

        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_prefill_uses_fifo_suggestions_when_requested(self):
        form = PaymentForm(
            admission=self.admission,
            user=self.accounts,
            suggestion_amount=Decimal("12000.00"),
            prefill_allocations=True,
        )

        self.assertEqual(
            form.fields[form.allocation_field_name(self.application)].initial,
            Decimal("1000.00"),
        )
        self.assertEqual(
            form.fields[form.allocation_field_name(self.installments[1])].initial,
            Decimal("10000.00"),
        )


class CreditApplicationFormTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))
        self.application = self.installments[0]
        self.course_two = self.installments[3]

    def make_lot(self, amount):
        payment = self.make_payment(admission=self.admission, amount=amount, allocations=[])
        return self.credit_lot(payment)

    def form(self, data):
        return CreditApplicationForm(data, admission=self.admission)

    def test_valid_application(self):
        self.make_lot(Decimal("10000.00"))
        form = self.form({"installment": self.application.pk, "amount": "1000.00", "reason": ""})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["installment"], self.application)
        self.assertEqual(form.cleaned_data["amount"], Decimal("1000.00"))

    def test_amount_above_outstanding_is_rejected(self):
        self.make_payment(
            admission=self.admission,
            amount=Decimal("20000.00"),
            allocations=[{"installment": self.course_two, "amount": Decimal("20000.00")}],
        )
        self.make_lot(Decimal("100000.00"))
        form = self.form({"installment": self.course_two.pk, "amount": "35000.00"})

        self.assertFalse(form.is_valid())
        self.assertIn("exceeds this installment", " ".join(form.non_field_errors()))

    def test_amount_above_available_credit_is_rejected(self):
        self.make_lot(Decimal("5000.00"))
        form = self.form({"installment": self.application.pk, "amount": "6000.00"})

        self.assertFalse(form.is_valid())
        self.assertIn("exceeds the available advance credit", " ".join(form.non_field_errors()))

    def test_queryset_excludes_fully_paid_installments(self):
        self.make_payment(
            admission=self.admission,
            amount=Decimal("1000.00"),
            allocations=[{"installment": self.application, "amount": Decimal("1000.00")}],
        )
        self.make_lot(Decimal("5000.00"))

        form = self.form({"installment": self.application.pk, "amount": "100.00"})

        self.assertNotIn(self.application, form.fields["installment"].queryset)
        self.assertFalse(form.is_valid())
        self.assertIn("installment", form.errors)


class RefundFormTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.payment = self.make_payment(admission=self.admission, amount=Decimal("250000.00"))
        self.lot = self.credit_lot(self.payment)

    def data(self, **overrides):
        data = {
            "credit": self.lot.pk,
            "amount": "1000.00",
            "reason": REASON,
            "mode": "BANK_TRANSFER",
            "request_key": "form-refund",
        }
        data.update(overrides)
        return data

    def test_valid_refund_request(self):
        form = RefundForm(self.data(), admission=self.admission)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["credit"], self.lot)

    def test_short_reason_is_rejected(self):
        form = RefundForm(self.data(reason="short"), admission=self.admission)

        self.assertFalse(form.is_valid())
        self.assertIn("reason", form.errors)

    def test_amount_above_lot_remaining_is_rejected(self):
        form = RefundForm(self.data(amount="60000.00"), admission=self.admission)

        self.assertFalse(form.is_valid())
        self.assertIn(
            "exceeds the available refundable credit",
            " ".join(form.non_field_errors()),
        )

    def test_only_active_lots_with_a_balance_are_offered(self):
        refund = request_refund(
            self.lot,
            amount=Decimal("52350.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="form-deplete",
        )
        decide_refund(refund, approve=True, actor=self.admin)

        form = RefundForm(admission=self.admission)

        self.assertFalse(form.fields["credit"].queryset.exists())
        invalid = RefundForm(self.data(), admission=self.admission)
        self.assertFalse(invalid.is_valid())
        self.assertIn("credit", invalid.errors)

    def test_form_without_an_admission_has_no_lots(self):
        form = RefundForm()

        self.assertFalse(form.fields["credit"].queryset.exists())


class DecisionFormTests(PaymentTestBase):
    def test_approve_and_reject_are_valid_decisions(self):
        for decision in ("approve", "reject"):
            with self.subTest(decision=decision):
                form = DecisionForm({"decision": decision, "note": "Noted."})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["decision"], decision)

    def test_decision_is_required(self):
        form = DecisionForm({"note": "No decision supplied."})

        self.assertFalse(form.is_valid())
        self.assertIn("decision", form.errors)

    def test_refund_decision_accepts_an_optional_actual_date(self):
        form = RefundDecisionForm({"decision": "approve", "refunded_on": "2026-08-01"})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["refunded_on"], date(2026, 8, 1))

        without_date = RefundDecisionForm({"decision": "approve"})
        self.assertTrue(without_date.is_valid(), without_date.errors)
        self.assertIsNone(without_date.cleaned_data["refunded_on"])

    def test_refund_decision_rejects_an_invalid_date(self):
        form = RefundDecisionForm({"decision": "approve", "refunded_on": "31/08/2026"})

        self.assertFalse(form.is_valid())
        self.assertIn("refunded_on", form.errors)

    def test_reversal_form_requires_a_ten_character_reason(self):
        valid = PaymentReversalForm({"reason": REASON, "request_key": "k"})
        self.assertTrue(valid.is_valid(), valid.errors)

        invalid = PaymentReversalForm({"reason": "short", "request_key": "k"})
        self.assertFalse(invalid.is_valid())
        self.assertIn("reason", invalid.errors)


class CreditFormIntegrationTests(PaymentTestBase):
    """The form's accepted values are the values the service can consume."""

    def test_form_rows_can_be_applied_by_the_service(self):
        plan = self.make_approved_plan()
        installment = plan.installments.get(sequence=2)
        payment = self.make_payment(
            admission=plan.admission, amount=Decimal("5000.00"), allocations=[]
        )
        lot = self.credit_lot(payment)
        form = CreditApplicationForm(
            {"installment": installment.pk, "amount": "1500.00"},
            admission=plan.admission,
        )
        self.assertTrue(form.is_valid(), form.errors)

        applied = apply_advance_credit(
            form.cleaned_data["installment"],
            amount=form.cleaned_data["amount"],
            actor=self.accounts,
            request_key="form-service",
        )

        self.assertEqual(applied, Decimal("1500.00"))
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("3500.00"))

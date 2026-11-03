"""Selectors: centre scoping, multi-centre aggregates, balances, queues."""

from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum

from apps.fees.selectors import (
    admission_fee_summary,
)
from apps.fees.selectors import (
    installment_outstanding as fee_installment_outstanding,
)
from apps.payments.models import AdvanceCredit
from apps.payments.selectors import (
    admission_payment_total,
    available_credit,
    credit_history,
    credit_lots_for_user,
    fifo_suggestions,
    installment_allocated_total,
    installment_outstanding,
    payments_for_user,
    pending_refunds,
    pending_reversals,
)
from apps.payments.services import (
    apply_advance_credit,
    decide_payment_reversal,
    decide_refund,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for the selector-scope test."


class PaymentSelectorScopeTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.tvm_plan = self.make_approved_plan()
        self.tvm_admission = self.tvm_plan.admission
        self.koc_admission = self.make_admission(centre=self.other_centre)
        self.koc_plan = self.make_approved_plan(admission=self.koc_admission)

        # A collection at the admission's own centre.
        self.tvm_payment = self.make_payment(
            admission=self.tvm_admission,
            amount=Decimal("10000.00"),
            collection_centre=self.centre,
        )
        # A collection for the same admission at the other centre.
        self.tvm_via_koc = self.make_payment(
            admission=self.tvm_admission,
            amount=Decimal("5000.00"),
            collection_centre=self.other_centre,
            actor=self.admin,
        )
        # A collection for an admission belonging to the other centre.
        self.koc_payment = self.make_payment(
            admission=self.koc_admission,
            amount=Decimal("7000.00"),
            collection_centre=self.other_centre,
            actor=self.admin,
        )

    def test_accounts_only_sees_payments_for_accessible_admission_centres(self):
        visible = list(payments_for_user(self.accounts))

        self.assertEqual(
            {payment.pk for payment in visible},
            {self.tvm_payment.pk, self.tvm_via_koc.pk},
        )

    def test_admin_sees_payments_for_every_centre(self):
        visible = list(payments_for_user(self.admin))

        self.assertEqual(len(visible), 3)
        self.assertEqual(
            {payment.pk for payment in visible},
            {self.tvm_payment.pk, self.tvm_via_koc.pk, self.koc_payment.pk},
        )

    def test_other_centre_staff_only_sees_its_own_admissions(self):
        visible = list(payments_for_user(self.other_admissions))

        self.assertEqual([payment.pk for payment in visible], [self.koc_payment.pk])

    def test_collection_centre_and_admission_centre_are_distinct_dimensions(self):
        by_collection = {
            row["centre__code"]: row["total"]
            for row in payments_for_user(self.admin)
            .order_by()
            .values("centre__code")
            .annotate(total=Sum("amount"))
        }
        by_admission = {
            row["admission__centre__code"]: row["total"]
            for row in payments_for_user(self.admin)
            .order_by()
            .values("admission__centre__code")
            .annotate(total=Sum("amount"))
        }

        self.assertEqual(by_collection, {"TVM": Decimal("10000.00"), "KOC": Decimal("12000.00")})
        self.assertEqual(by_admission, {"TVM": Decimal("15000.00"), "KOC": Decimal("7000.00")})

        # Aggregating never discards the underlying individual records.
        crumbs = (
            payments_for_user(self.admin)
            .order_by()
            .values("centre__code")
            .annotate(records=Count("pk"))
        )
        self.assertEqual(
            {row["centre__code"]: row["records"] for row in crumbs},
            {"TVM": 1, "KOC": 2},
        )

    def test_credit_lots_are_scoped_by_admission_centre(self):
        payment = self.make_payment(
            admission=self.tvm_admission,
            amount=Decimal("250000.00"),
            collection_centre=self.other_centre,
            actor=self.admin,
        )
        lot = self.credit_lot(payment)

        self.assertEqual([row.pk for row in credit_lots_for_user(self.accounts)], [lot.pk])
        self.assertFalse(credit_lots_for_user(self.other_admissions).exists())

    def test_pending_refunds_are_scoped_and_filterable(self):
        payment = self.make_payment(admission=self.tvm_admission, amount=Decimal("250000.00"))
        lot = self.credit_lot(payment)
        refund = request_refund(
            lot,
            amount=Decimal("1000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="selector-refund",
        )

        self.assertEqual([row.pk for row in pending_refunds(self.accounts)], [refund.pk])
        self.assertFalse(pending_refunds(self.other_admissions).exists())
        self.assertEqual(
            [row.pk for row in pending_refunds(self.admin, centre=self.centre)],
            [refund.pk],
        )
        self.assertFalse(pending_refunds(self.admin, centre=self.other_centre).exists())

        decide_refund(refund, approve=True, actor=self.admin)
        self.assertFalse(pending_refunds(self.admin).exists())

    def test_pending_reversals_are_scoped_and_filterable(self):
        reversal = request_payment_reversal(
            self.tvm_payment,
            reason=REASON,
            actor=self.accounts,
            request_key="selector-reversal",
        )

        self.assertEqual([row.pk for row in pending_reversals(self.accounts)], [reversal.pk])
        self.assertFalse(pending_reversals(self.other_admissions).exists())
        self.assertEqual(
            [row.pk for row in pending_reversals(self.admin, centre=self.centre)],
            [reversal.pk],
        )
        self.assertFalse(pending_reversals(self.admin, centre=self.other_centre).exists())

        decide_payment_reversal(reversal, approve=False, actor=self.admin)
        self.assertFalse(pending_reversals(self.admin).exists())

    def test_admission_payment_total_ignores_reversed_collections(self):
        other = self.make_payment(admission=self.tvm_admission, amount=Decimal("2000.00"))
        self.assertEqual(admission_payment_total(self.tvm_admission), Decimal("17000.00"))

        reversal = request_payment_reversal(
            other, reason=REASON, actor=self.accounts, request_key="selector-close"
        )
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        self.assertEqual(admission_payment_total(self.tvm_admission), Decimal("15000.00"))


class SelectorBalanceTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.installments = list(self.plan.installments.order_by("sequence"))

    def test_fifo_suggestions_are_capped_at_each_outstanding(self):
        rows = fifo_suggestions(self.admission, Decimal("50000.00"))

        self.assertEqual(
            [row.suggested_amount for row in rows],
            [
                Decimal("1000.00"),
                Decimal("10000.00"),
                Decimal("39000.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            ],
        )
        self.assertEqual(
            [row.outstanding for row in rows],
            [installment.amount for installment in self.installments],
        )

    def test_fifo_suggestions_skip_already_paid_installments(self):
        self.make_payment(
            admission=self.admission,
            amount=Decimal("1000.00"),
            allocations=[{"installment": self.installments[0], "amount": Decimal("1000.00")}],
        )

        rows = fifo_suggestions(self.admission, Decimal("500.00"))

        self.assertEqual(rows[0].suggested_amount, Decimal("0.00"))
        self.assertEqual(rows[1].suggested_amount, Decimal("500.00"))

    def test_installment_totals_and_outstanding_are_derived_from_rows(self):
        payment = self.make_payment(
            admission=self.admission,
            amount=Decimal("12000.00"),
            allocations=[
                {"installment": self.installments[0], "amount": Decimal("1000.00")},
                {"installment": self.installments[1], "amount": Decimal("10000.00")},
                {"installment": self.installments[2], "amount": Decimal("1000.00")},
            ],
        )

        self.assertEqual(installment_allocated_total(self.installments[0]), Decimal("1000.00"))
        self.assertEqual(installment_outstanding(self.installments[0]), Decimal("0.00"))
        self.assertEqual(installment_outstanding(self.installments[2]), Decimal("39000.00"))
        self.assertEqual(payment.amount, Decimal("12000.00"))

        summary = admission_fee_summary(self.admission, date(2026, 7, 15))
        self.assertEqual(summary.total_paid, Decimal("12000.00"))
        self.assertEqual(summary.total_outstanding, Decimal("185650.00"))
        self.assertEqual(fee_installment_outstanding(self.installments[2]), Decimal("39000.00"))

    def test_available_credit_sums_only_active_lots(self):
        payment = self.make_payment(
            admission=self.admission, amount=Decimal("52350.00"), allocations=[]
        )
        lot = self.credit_lot(payment)
        self.assertEqual(available_credit(self.admission), Decimal("52350.00"))

        apply_advance_credit(
            self.installments[2],
            amount=Decimal("10000.00"),
            actor=self.accounts,
            request_key="selector-credit",
        )
        self.assertEqual(available_credit(self.admission), Decimal("42350.00"))

        refund = request_refund(
            lot,
            amount=Decimal("42350.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="selector-deplete",
        )
        decide_refund(refund, approve=True, actor=self.admin)
        self.assertEqual(available_credit(self.admission), Decimal("0.00"))
        self.assertFalse(
            AdvanceCredit.objects.filter(
                admission=self.admission, status=AdvanceCredit.Status.ACTIVE
            ).exists()
        )

    def test_credit_history_orders_lots_and_includes_children(self):
        first_payment = self.make_payment(
            admission=self.admission, amount=Decimal("3000.00"), allocations=[]
        )
        second_payment = self.make_payment(
            admission=self.admission, amount=Decimal("5000.00"), allocations=[]
        )
        first_lot = self.credit_lot(first_payment)
        second_lot = self.credit_lot(second_payment)
        refund = request_refund(
            first_lot,
            amount=Decimal("1000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="selector-history",
        )
        decide_refund(refund, approve=True, actor=self.admin)

        history = credit_history(self.admission)

        self.assertEqual([lot.pk for lot in history], [first_lot.pk, second_lot.pk])
        self.assertEqual(history[0].refund_allocations.count(), 1)
        self.assertEqual(history[1].allocations.count(), 0)

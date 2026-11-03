"""Role and centre-scoping matrix for the payment-domain services."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied

from apps.admissions.models import Admission
from apps.payments.models import AdvanceCredit, Payment, PaymentReversal, Refund
from apps.payments.services import (
    decide_payment_reversal,
    decide_refund,
    record_payment,
    request_payment_reversal,
    request_refund,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for this permission-matrix test."


class PaymentRoleMatrixTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission
        self.outsider_accounts = self._make_accounts("outsider_matrix", self.other_centre)

    def _make_accounts(self, username, centre):
        from apps.fees.tests.factories import make_user

        user = make_user(username, role="ACCOUNTS")
        user.centres.add(centre)
        return user

    def record(self, *, actor=None, admission=None, centre=None, **overrides):
        kwargs = {
            "amount": Decimal("1000.00"),
            "paid_on": date(2026, 7, 15),
            "mode": "CASH",
            "collection_centre": centre or self.centre,
            "actor": actor or self.accounts,
        }
        kwargs.update(overrides)
        return record_payment(admission or self.admission, **kwargs)

    # -- recording ---------------------------------------------------------
    def test_admin_management_and_accounts_can_record_payments(self):
        for actor in (self.admin, self.management, self.accounts):
            with self.subTest(role=actor.role):
                admission = self.make_admission()
                self.make_approved_plan(admission=admission, actor=self.admin)
                payment = self.record(admission=admission, actor=actor)
                self.assertEqual(payment.status, Payment.Status.POSTED)
                self.assertEqual(payment.recorded_by, actor)

    def test_admissions_staff_cannot_record_payments(self):
        with self.assertRaises(PermissionDenied):
            self.record(actor=self.admissions)

        self.assertFalse(Payment.objects.exists())

    def test_anonymous_actor_cannot_record_payments(self):
        from django.contrib.auth.models import AnonymousUser

        with self.assertRaises(PermissionDenied):
            self.record(actor=AnonymousUser())

    def test_cross_centre_actor_cannot_record_a_payment(self):
        with self.assertRaises(PermissionDenied):
            self.record(actor=self.outsider_accounts)

        self.assertFalse(Payment.objects.exists())

    def test_collection_centre_must_be_accessible_to_the_actor(self):
        # Accounts can see the admission's centre but not the centre that
        # physically collected the money.
        with self.assertRaises(PermissionDenied):
            self.record(centre=self.other_centre, actor=self.accounts)

        self.assertFalse(Payment.objects.exists())

    def test_collection_centre_may_differ_from_the_admission_centre(self):
        self.accounts.centres.add(self.other_centre)

        payment = self.record(centre=self.other_centre)

        self.assertEqual(payment.centre, self.other_centre)
        self.assertEqual(payment.admission.centre, self.centre)
        self.assertEqual(payment.receipt.centre, self.other_centre)

    def test_payment_requires_an_enrolled_admission(self):
        admission = self.make_admission(status=Admission.Status.ENQUIRED)
        self.make_plan_row(admission=admission, status="APPROVED")

        with self.assertRaisesMessage(ValueError, "enrolled admission"):
            self.record(admission=admission)

    def test_payment_requires_an_approved_fee_plan(self):
        admission = self.make_admission()
        self.make_draft_plan(admission=admission, actor=self.admin)

        with self.assertRaisesMessage(ValueError, "approved fee plan is required"):
            self.record(admission=admission)

    # -- refunds -----------------------------------------------------------
    def make_lot(self):
        payment = record_payment(
            self.admission,
            amount=Decimal("250000.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            collection_centre=self.centre,
            actor=self.accounts,
        )
        return AdvanceCredit.objects.get(source_payment=payment)

    def test_accounts_can_request_but_not_decide_refunds(self):
        lot = self.make_lot()
        refund = request_refund(
            lot,
            amount=Decimal("1000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="matrix-refund",
        )
        self.assertEqual(refund.status, Refund.Status.PENDING)

        with self.assertRaises(PermissionDenied):
            decide_refund(refund, approve=True, actor=self.accounts)

    def test_admissions_staff_cannot_request_or_decide_refunds(self):
        lot = self.make_lot()

        with self.assertRaises(PermissionDenied):
            request_refund(
                lot,
                amount=Decimal("1000.00"),
                reason=REASON,
                mode="CASH",
                actor=self.admissions,
                request_key="matrix-admissions-refund",
            )

        refund = request_refund(
            lot,
            amount=Decimal("1000.00"),
            reason=REASON,
            mode="CASH",
            actor=self.accounts,
            request_key="matrix-admissions-decide",
        )
        with self.assertRaises(PermissionDenied):
            decide_refund(refund, approve=True, actor=self.admissions)

    def test_cross_centre_accounts_cannot_request_a_refund(self):
        lot = self.make_lot()

        with self.assertRaises(PermissionDenied):
            request_refund(
                lot,
                amount=Decimal("1000.00"),
                reason=REASON,
                mode="CASH",
                actor=self.outsider_accounts,
                request_key="cross-centre-refund",
            )

        self.assertFalse(Refund.objects.exists())

    def test_admin_and_management_decide_refunds_across_centres(self):
        # ADMIN/MANAGEMENT are documented all-centre roles; the gate on the
        # decision path is the role, not a centre assignment.
        lot = self.make_lot()
        for index, actor in enumerate((self.admin, self.management)):
            refund = request_refund(
                lot,
                amount=Decimal("1000.00"),
                reason=REASON,
                mode="CASH",
                actor=self.accounts,
                request_key=f"cross-centre-approve-{index}",
            )
            decided = decide_refund(refund, approve=True, actor=actor)
            self.assertEqual(decided.status, Refund.Status.APPROVED)
            self.assertEqual(decided.decided_by, actor)

    # -- reversals ---------------------------------------------------------
    def test_accounts_can_request_but_not_decide_reversals(self):
        payment = self.record()
        reversal = request_payment_reversal(
            payment, reason=REASON, actor=self.accounts, request_key="matrix-reversal"
        )
        self.assertEqual(reversal.status, PaymentReversal.Status.PENDING)

        with self.assertRaises(PermissionDenied):
            decide_payment_reversal(reversal, approve=True, actor=self.accounts)

    def test_admissions_staff_cannot_request_or_decide_reversals(self):
        payment = self.record()

        with self.assertRaises(PermissionDenied):
            request_payment_reversal(
                payment,
                reason=REASON,
                actor=self.admissions,
                request_key="matrix-admissions-reversal",
            )

        reversal = request_payment_reversal(
            payment,
            reason=REASON,
            actor=self.accounts,
            request_key="matrix-admissions-decide-reversal",
        )
        with self.assertRaises(PermissionDenied):
            decide_payment_reversal(reversal, approve=True, actor=self.admissions)

    def test_cross_centre_accounts_cannot_request_a_reversal(self):
        payment = self.record()

        with self.assertRaises(PermissionDenied):
            request_payment_reversal(
                payment,
                reason=REASON,
                actor=self.outsider_accounts,
                request_key="cross-centre-reversal",
            )

        self.assertFalse(PaymentReversal.objects.exists())

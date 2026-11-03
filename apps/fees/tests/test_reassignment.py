"""A4: controlled fee-plan reassignment with reconciliation.

The reassignment path must never rewrite an installment, delete a payment or
receipt, or lose money. These tests assert the permission split, the snapshot
and audit rows, the state transition, and a conservation equation across the
reconciliation.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.fees.models import FeePlan, FeePlanReassignment
from apps.fees.reassignment_services import (
    decide_fee_plan_reassignment,
    request_fee_plan_reassignment,
)
from apps.fees.selectors import pending_reassignments
from apps.fees.services import (
    cancel_draft_fee_plan,
    create_fee_plan_version,
    update_draft_fee_plan,
)
from apps.fees.tests.factories import (
    GST_RATE,
    default_components,
    make_user,
)
from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
    Payment,
    Receipt,
)
from apps.payments.selectors import available_credit
from apps.payments.services import (
    apply_advance_credit,
    request_payment_reversal,
)
from apps.payments.tests.factories import PaymentTestBase

REASON = "Documented business reason for revising this admission's fee plan."
SHORT_REASON = "short"

# (course_fee at 18% GST, four course-installment amounts) for round totals.
REVISED_PLANS = {
    Decimal("300000.00"): (
        Decimal("254237.29"),
        (Decimal("72000.00"), Decimal("72000.00"), Decimal("72000.00"), Decimal("73000.00")),
    ),
    Decimal("250000.00"): (
        Decimal("211864.41"),
        (Decimal("59000.00"), Decimal("60000.00"), Decimal("60000.00"), Decimal("60000.00")),
    ),
    Decimal("150000.00"): (
        Decimal("127118.64"),
        (Decimal("34000.00"), Decimal("35000.00"), Decimal("35000.00"), Decimal("35000.00")),
    ),
    Decimal("100000.00"): (
        Decimal("84745.76"),
        (Decimal("22000.00"), Decimal("22000.00"), Decimal("22000.00"), Decimal("23000.00")),
    ),
}


def revised_components(course_amounts):
    """Six valid components for a revised total, application/admission kept."""
    rows = default_components()
    rows[0]["amount"] = Decimal("1000.00")
    rows[1]["amount"] = Decimal("10000.00")
    for row, amount in zip(rows[2:], course_amounts, strict=True):
        row["amount"] = amount
    return rows


class ReassignmentTestBase(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.admission = self.make_admission()
        self.plan = self.make_approved_plan(admission=self.admission)
        self.target = self.revise(self.plan, Decimal("300000.00"))

    def revise(self, plan, total=Decimal("300000.00"), *, actor=None):
        """Create the next draft version and set it to ``total``."""
        actor = actor or self.accounts
        target = create_fee_plan_version(plan, actor)
        if total != plan.total_payable:
            course_fee, course_amounts = REVISED_PLANS[total]
            update_draft_fee_plan(
                target,
                course_fee=course_fee,
                gst_rate=GST_RATE,
                components=revised_components(course_amounts),
                actor=actor,
            )
            target.refresh_from_db()
        return target

    def make_pair(self, total=Decimal("300000.00"), *, actor=None):
        """A fresh admission with an approved plan and a revised draft."""
        admission = self.make_admission()
        plan = self.make_approved_plan(admission=admission)
        return admission, plan, self.revise(plan, total, actor=actor or self.accounts)

    def request(self, target=None, *, actor=None, key="reassign-1", reason=REASON):
        return request_fee_plan_reassignment(
            target or self.target,
            reason=reason,
            actor=actor or self.accounts,
            request_key=key,
        )

    def decide(self, reassignment, *, approve=True, actor=None, note=""):
        return decide_fee_plan_reassignment(
            reassignment, approve=approve, actor=actor or self.admin, note=note
        )

    def active_allocation_total(self):
        return InstallmentAllocation.objects.filter(
            status=InstallmentAllocation.Status.ACTIVE
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


class ReassignmentRequestTests(ReassignmentTestBase):
    def test_accounts_can_request_a_reassignment(self):
        reassignment = self.request()

        self.assertEqual(reassignment.status, FeePlanReassignment.Status.PENDING)
        self.assertEqual(reassignment.admission, self.admission)
        self.assertEqual(reassignment.from_plan, self.plan)
        self.assertEqual(reassignment.to_plan, self.target)
        self.assertEqual(reassignment.previous_total, Decimal("197650.00"))
        self.assertEqual(reassignment.proposed_total, Decimal("300000.00"))
        self.assertEqual(reassignment.difference, Decimal("102350.00"))
        self.assertFalse(reassignment.had_payments)
        self.assertEqual(reassignment.requested_by, self.accounts)
        self.assertIsNone(reassignment.decided_by)
        self.assertEqual(reassignment.request_key, "reassign-1")
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REASSIGNMENT_REQUESTED,
                actor=self.accounts,
                amount=Decimal("102350.00"),
            ).exists()
        )

    def test_admissions_staff_cannot_request(self):
        with self.assertRaises(PermissionDenied):
            self.request(actor=self.admissions)

        self.assertFalse(FeePlanReassignment.objects.exists())

    def test_out_of_centre_accounts_cannot_request(self):
        outsider = make_user("reassignment_outsider", role="ACCOUNTS")
        outsider.centres.add(self.other_centre)

        with self.assertRaises(PermissionDenied):
            self.request(actor=outsider)

        self.assertFalse(FeePlanReassignment.objects.exists())

    def test_reason_shorter_than_ten_characters_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            self.request(reason=SHORT_REASON)

        self.assertFalse(FeePlanReassignment.objects.exists())

    def test_target_must_be_an_unapproved_draft(self):
        with self.assertRaisesMessage(ValueError, "unapproved draft"):
            self.request(target=self.plan)

        self.assertFalse(FeePlanReassignment.objects.exists())

    def test_admission_needs_an_approved_plan_to_reassign_from(self):
        admission = self.make_admission()
        draft = self.make_plan_row(admission=admission, version=1, status=FeePlan.Status.DRAFT)

        with self.assertRaisesMessage(ValueError, "no approved fee plan"):
            self.request(target=draft)

    def test_target_must_be_a_newer_version(self):
        admission = self.make_admission()
        self.make_approved_plan(admission=admission)
        older = self.make_plan_row(admission=admission, version=0, status=FeePlan.Status.DRAFT)

        with self.assertRaisesMessage(ValueError, "new fee-plan version"):
            self.request(target=older)

    def test_only_one_pending_request_per_admission(self):
        self.request()
        second_target = self.make_plan_row(
            admission=self.admission, version=3, status=FeePlan.Status.DRAFT
        )

        with self.assertRaisesMessage(ValueError, "already pending"):
            self.request(target=second_target, key="second-pending")

        self.assertEqual(FeePlanReassignment.objects.count(), 1)

    def test_database_enforces_one_pending_request_per_admission(self):
        self.request()
        second_target = self.make_plan_row(
            admission=self.admission, version=3, status=FeePlan.Status.DRAFT
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            FeePlanReassignment.objects.create(
                admission=self.admission,
                from_plan=self.plan,
                to_plan=second_target,
                reason=REASON,
                previous_total=Decimal("197650.00"),
                proposed_total=Decimal("0.00"),
                difference=Decimal("-197650.00"),
                requested_by=self.accounts,
                request_key="db-second-pending",
            )

    def test_same_request_key_returns_the_same_request(self):
        first = self.request()
        second = self.request()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(FeePlanReassignment.objects.count(), 1)
        self.assertEqual(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REASSIGNMENT_REQUESTED
            ).count(),
            1,
        )

    def test_request_key_cannot_be_reused_for_another_admission(self):
        first = self.request()
        _, _, other_target = self.make_pair()

        with self.assertRaisesMessage(ValueError, "belongs to another reassignment request"):
            self.request(target=other_target, key=first.request_key)

        self.assertEqual(FeePlanReassignment.objects.count(), 1)

    def test_request_key_cannot_be_reused_for_another_target_of_the_same_admission(self):
        first = self.request()
        self.decide(first, approve=False, note="Replaced by a corrected proposal.")
        cancel_draft_fee_plan(self.target, self.admin, "Replaced by a corrected target.")
        replacement = self.revise(self.plan, Decimal("300000.00"))

        with self.assertRaisesMessage(ValueError, "belongs to another reassignment request"):
            self.request(target=replacement, key=first.request_key)

        self.assertEqual(FeePlanReassignment.objects.count(), 1)


class ReassignmentDecisionTests(ReassignmentTestBase):
    def test_accounts_and_admissions_cannot_decide(self):
        reassignment = self.request()

        for actor in (self.accounts, self.admissions):
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                self.decide(reassignment, approve=True, actor=actor)

        reassignment.refresh_from_db()
        self.assertEqual(reassignment.status, FeePlanReassignment.Status.PENDING)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.APPROVED)

    def test_admin_and_management_can_approve(self):
        for actor in (self.admin, self.management):
            _, plan, target = self.make_pair()
            reassignment = request_fee_plan_reassignment(
                target,
                reason=REASON,
                actor=self.accounts,
                request_key=f"approve-{actor.role}",
            )
            with self.subTest(role=actor.role):
                decided = self.decide(reassignment, approve=True, actor=actor, note="Approved.")
                self.assertEqual(decided.status, FeePlanReassignment.Status.APPROVED)
                self.assertEqual(decided.decided_by, actor)
                self.assertIsNotNone(decided.decided_at)
                self.assertEqual(decided.decision_note, "Approved.")

                plan.refresh_from_db()
                target.refresh_from_db()
                self.assertEqual(plan.status, FeePlan.Status.SUPERSEDED)
                self.assertEqual(target.status, FeePlan.Status.APPROVED)
                self.assertEqual(target.approved_by, actor)

    def test_management_can_approve_a_cross_centre_request(self):
        admission = self.make_admission(centre=self.other_centre)
        plan = self.make_approved_plan(admission=admission)
        target = self.revise(plan, Decimal("300000.00"), actor=self.admin)
        reassignment = request_fee_plan_reassignment(
            target, reason=REASON, actor=self.admin, request_key="cross-centre"
        )

        decided = self.decide(reassignment, approve=True, actor=self.management)

        self.assertEqual(decided.status, FeePlanReassignment.Status.APPROVED)
        plan.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(plan.status, FeePlan.Status.SUPERSEDED)
        self.assertEqual(target.status, FeePlan.Status.APPROVED)

    def test_rejection_leaves_the_plans_untouched(self):
        reassignment = self.request()

        decided = self.decide(
            reassignment, approve=False, actor=self.admin, note="Requires rework."
        )

        self.assertEqual(decided.status, FeePlanReassignment.Status.REJECTED)
        self.assertEqual(decided.decided_by, self.admin)
        self.assertEqual(decided.decision_note, "Requires rework.")
        self.plan.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.APPROVED)
        self.assertEqual(self.target.status, FeePlan.Status.DRAFT)
        self.assertFalse(InstallmentAllocation.objects.exists())
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REASSIGNMENT_REJECTED,
                actor=self.admin,
            ).exists()
        )

    def test_a_decided_request_cannot_be_decided_again(self):
        reassignment = self.request()
        self.decide(reassignment, approve=True)
        reassignment.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "Only pending reassignment requests"):
            self.decide(reassignment, approve=False)

    def test_approval_rechecks_that_the_target_is_still_a_draft(self):
        reassignment = self.request()
        cancel_draft_fee_plan(self.target, self.admin, "Target withdrawn before approval.")

        with self.assertRaisesMessage(ValueError, "no longer a draft"):
            self.decide(reassignment, approve=True)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.APPROVED)
        reassignment.refresh_from_db()
        self.assertEqual(reassignment.status, FeePlanReassignment.Status.PENDING)


class ReassignmentReconciliationTests(ReassignmentTestBase):
    def test_approval_without_payments_only_switches_plans(self):
        reassignment = self.request()

        decided = self.decide(reassignment, approve=True)

        decided.refresh_from_db()
        self.assertFalse(decided.had_payments)
        self.plan.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.SUPERSEDED)
        self.assertEqual(self.target.status, FeePlan.Status.APPROVED)
        self.assertFalse(AdvanceCredit.objects.exists())
        self.assertFalse(InstallmentAllocation.objects.exists())
        self.assertEqual(
            decided.difference,
            self.target.total_payable - self.plan.total_payable,
        )
        self.assertTrue(
            FinancialAuditEvent.objects.filter(
                event_type=FinancialAuditEvent.Event.REASSIGNMENT_APPROVED,
                actor=self.admin,
            ).exists()
        )

    def test_approval_preserves_payments_and_receipts_and_conserves_money(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("12000.00"))
        receipt_number = payment.receipt.number
        before = self.active_allocation_total() + available_credit(self.admission)
        self.assertEqual(before, Decimal("12000.00"))
        reassignment = self.request()
        reassignment.refresh_from_db()
        self.assertTrue(reassignment.had_payments)

        decided = self.decide(reassignment, approve=True)

        decided.refresh_from_db()
        self.assertEqual(decided.status, FeePlanReassignment.Status.APPROVED)
        payment.refresh_from_db()
        payment.receipt.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.POSTED)
        self.assertEqual(payment.receipt.status, Receipt.Status.ACTIVE)
        self.assertEqual(payment.receipt.number, receipt_number)
        self.assertEqual(payment.amount, Decimal("12000.00"))

        self.plan.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.SUPERSEDED)
        self.assertEqual(self.target.status, FeePlan.Status.APPROVED)
        for allocation in payment.allocations.all():
            self.assertEqual(allocation.status, InstallmentAllocation.Status.REVERSED)
            self.assertEqual(allocation.reversed_by, self.admin)

        lot = AdvanceCredit.objects.get(source_type=AdvanceCredit.Source.REASSIGNMENT)
        self.assertIsNone(lot.source_payment)
        self.assertEqual(lot.admission, self.admission)
        self.assertEqual(lot.original_amount, Decimal("12000.00"))
        self.assertEqual(lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(lot.status, AdvanceCredit.Status.DEPLETED)

        new_rows = InstallmentAllocation.objects.filter(
            installment__fee_plan=self.target,
            status=InstallmentAllocation.Status.ACTIVE,
        ).order_by("installment__sequence")
        self.assertEqual(
            [row.amount for row in new_rows],
            [Decimal("1000.00"), Decimal("10000.00"), Decimal("1000.00")],
        )
        self.assertTrue(all(row.credit_id == lot.pk for row in new_rows))
        self.assertEqual(
            self.active_allocation_total() + available_credit(self.admission),
            before,
            "Reconciliation must conserve the money it moves.",
        )

    def test_credit_funded_allocations_are_reversed_and_returned(self):
        payment = self.make_payment(
            admission=self.admission, amount=Decimal("250000.00"), allocations=[]
        )
        lot = self.credit_lot(payment)
        self.assertEqual(lot.remaining_amount, Decimal("250000.00"))
        course_one = self.plan.installments.get(sequence=3)
        apply_advance_credit(
            course_one,
            amount=Decimal("10000.00"),
            actor=self.accounts,
            request_key="credit-before-reassignment",
        )
        credit_allocation = InstallmentAllocation.objects.get(
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT
        )

        before = self.active_allocation_total() + available_credit(self.admission)
        self.assertEqual(before, Decimal("250000.00"))
        reassignment = self.request()
        decided = self.decide(reassignment, approve=True)

        decided.refresh_from_db()
        credit_allocation.refresh_from_db()
        self.assertEqual(credit_allocation.status, InstallmentAllocation.Status.REVERSED)
        self.assertEqual(credit_allocation.reversed_by, self.admin)
        lot.refresh_from_db()
        self.assertEqual(lot.remaining_amount, Decimal("0.00"))
        self.assertEqual(lot.status, AdvanceCredit.Status.DEPLETED)

        self.target.refresh_from_db()
        self.assertEqual(self.target.status, FeePlan.Status.APPROVED)
        new_rows = InstallmentAllocation.objects.filter(
            installment__fee_plan=self.target,
            status=InstallmentAllocation.Status.ACTIVE,
        )
        self.assertEqual(
            sum((row.amount for row in new_rows), Decimal("0.00")),
            Decimal("250000.00"),
        )
        self.assertEqual(
            self.active_allocation_total() + available_credit(self.admission),
            before,
            "Credit-funded allocations must return to the pool, not vanish.",
        )

    def test_smaller_new_plan_leaves_the_unapplied_balance_as_credit(self):
        self.make_payment(admission=self.admission, amount=self.plan.total_payable)
        admission = self.admission
        # setUp already created a 3,00,000 target; withdraw it and propose a
        # smaller revised plan instead.
        cancel_draft_fee_plan(self.target, self.admin, "Not the proposal under test.")
        target = self.revise(self.plan, Decimal("150000.00"))

        before = self.active_allocation_total() + available_credit(admission)
        self.assertEqual(before, Decimal("197650.00"))
        reassignment = self.request(target=target, key="smaller-plan")
        self.decide(reassignment, approve=True)

        self.plan.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.SUPERSEDED)
        self.assertEqual(target.status, FeePlan.Status.APPROVED)
        self.assertEqual(available_credit(admission), Decimal("47650.00"))
        new_rows = InstallmentAllocation.objects.filter(
            installment__fee_plan=target,
            status=InstallmentAllocation.Status.ACTIVE,
        )
        self.assertEqual(
            sum((row.amount for row in new_rows), Decimal("0.00")),
            Decimal("150000.00"),
        )
        self.assertEqual(self.active_allocation_total() + available_credit(admission), before)

    def test_payment_reversal_is_blocked_after_an_approved_reassignment(self):
        payment = self.make_payment(admission=self.admission, amount=Decimal("1000.00"))
        reassignment = self.request()
        self.decide(reassignment, approve=True)

        with self.assertRaisesMessage(ValueError, "approved fee-plan reassignment"):
            request_payment_reversal(
                payment,
                reason=REASON,
                actor=self.accounts,
                request_key="reversal-after-reassignment",
            )


class ReassignmentSelectorTests(ReassignmentTestBase):
    def test_pending_requests_are_centre_scoped(self):
        reassignment = self.request()

        self.assertEqual(
            [row.pk for row in pending_reassignments(self.accounts)], [reassignment.pk]
        )
        self.assertFalse(pending_reassignments(self.other_admissions).exists())
        self.assertEqual([row.pk for row in pending_reassignments(self.admin)], [reassignment.pk])
        self.assertEqual(
            [row.pk for row in pending_reassignments(self.admin, centre=self.centre)],
            [reassignment.pk],
        )
        self.assertFalse(pending_reassignments(self.admin, centre=self.other_centre).exists())

    def test_a_request_at_another_centre_is_only_visible_there(self):
        admission = self.make_admission(centre=self.other_centre)
        plan = self.make_approved_plan(admission=admission)
        target = self.revise(plan, Decimal("300000.00"), actor=self.admin)
        reassignment = request_fee_plan_reassignment(
            target, reason=REASON, actor=self.admin, request_key="other-centre"
        )

        self.assertFalse(pending_reassignments(self.accounts).filter(pk=reassignment.pk).exists())
        self.assertTrue(
            pending_reassignments(self.other_admissions).filter(pk=reassignment.pk).exists()
        )
        self.assertFalse(
            pending_reassignments(self.admin, centre=self.centre)
            .filter(pk=reassignment.pk)
            .exists()
        )

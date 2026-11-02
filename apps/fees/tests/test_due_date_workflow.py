from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied

from apps.fees.models import DueDateChangeRequest, FeeInstallment, FeePlan
from apps.fees.selectors import effective_due_date
from apps.fees.services import (
    apply_due_date_revision,
    decide_due_date_change,
    request_due_date_change,
)
from apps.fees.tests.factories import GST_AMOUNT, TOTAL_PAYABLE, FeeTestBase, make_user

REASON = "Extending the due date after a documented student request."


class DueDateWorkflowTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]
        self.original = self.installment.original_due_date

    def make_request(self, *, installment=None, proposed=date(2026, 8, 15), actor=None):
        return request_due_date_change(
            installment or self.installment,
            proposed_due_date=proposed,
            reason=REASON,
            actor=actor or self.admissions,
        )

    # -- request-only path -------------------------------------------------
    def test_admissions_request_is_pending_and_does_not_move_the_date(self):
        change_request = self.make_request()

        self.assertEqual(change_request.status, DueDateChangeRequest.Status.PENDING)
        self.assertEqual(change_request.previous_due_date, self.original)
        self.assertEqual(change_request.proposed_due_date, date(2026, 8, 15))
        self.assertEqual(change_request.requested_by, self.admissions)
        self.assertEqual(effective_due_date(self.installment), self.original)
        self.assertFalse(self.installment.due_date_revisions.exists())

    def test_admissions_cannot_apply_a_revision_directly(self):
        with self.assertRaises(PermissionDenied):
            apply_due_date_revision(
                self.installment,
                revised_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=self.admissions,
            )

    def test_admissions_cannot_decide_a_request(self):
        change_request = self.make_request()

        with self.assertRaises(PermissionDenied):
            decide_due_date_change(change_request, approve=True, actor=self.admissions)

    def test_approved_request_creates_one_revision_and_moves_the_date(self):
        change_request = self.make_request()

        decided = decide_due_date_change(
            change_request, approve=True, actor=self.accounts, note="Approved by accounts."
        )

        decided.refresh_from_db()
        self.assertEqual(decided.status, DueDateChangeRequest.Status.APPROVED)
        self.assertEqual(decided.decided_by, self.accounts)
        self.assertIsNotNone(decided.decided_at)
        self.assertEqual(decided.decision_note, "Approved by accounts.")

        revisions = self.installment.due_date_revisions.all()
        self.assertEqual(revisions.count(), 1)
        revision = revisions.get()
        self.assertEqual(revision.previous_due_date, self.original)
        self.assertEqual(revision.revised_due_date, date(2026, 8, 15))
        self.assertEqual(revision.reason, REASON)
        self.assertEqual(revision.revised_by, self.accounts)
        self.assertEqual(revision.source_request, decided)
        self.assertIsNotNone(revision.created_at)
        self.assertEqual(effective_due_date(self.installment), date(2026, 8, 15))

    def test_rejected_request_creates_no_revision(self):
        change_request = self.make_request()

        decided = decide_due_date_change(change_request, approve=False, actor=self.admin)

        decided.refresh_from_db()
        self.assertEqual(decided.status, DueDateChangeRequest.Status.REJECTED)
        self.assertEqual(decided.decided_by, self.admin)
        self.assertIsNotNone(decided.decided_at)
        self.assertFalse(self.installment.due_date_revisions.exists())
        self.assertEqual(effective_due_date(self.installment), self.original)

    def test_already_decided_request_cannot_be_decided_again(self):
        change_request = self.make_request()
        decide_due_date_change(change_request, approve=False, actor=self.admin)
        change_request.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "Only pending requests"):
            decide_due_date_change(change_request, approve=True, actor=self.admin)

    # -- direct path -------------------------------------------------------
    def test_each_privileged_role_can_apply_directly(self):
        roles = (self.admin, self.management, self.accounts)

        for index, actor in enumerate(roles):
            installment = self.course_installments(self.plan)[index]
            revised = date(2026, 9, 10 + index)

            revision = apply_due_date_revision(
                installment,
                revised_due_date=revised,
                reason=REASON,
                actor=actor,
            )

            self.assertEqual(revision.revised_by, actor)
            self.assertIsNone(revision.source_request)
            self.assertEqual(effective_due_date(installment), revised)

    def test_each_privileged_role_can_decide_requests(self):
        roles = (self.admin, self.management, self.accounts)

        for index, actor in enumerate(roles):
            installment = self.course_installments(self.plan)[index]
            change_request = self.make_request(
                installment=installment,
                proposed=date(2026, 9, 10 + index),
            )

            decided = decide_due_date_change(change_request, approve=True, actor=actor)

            self.assertEqual(decided.status, DueDateChangeRequest.Status.APPROVED)
            self.assertEqual(decided.decided_by, actor)

    def test_direct_apply_allows_earlier_and_later_dates(self):
        later = date(2026, 10, 15)
        earlier = date(2026, 5, 1)

        apply_due_date_revision(
            self.installment,
            revised_due_date=later,
            reason="Moving the target later after a documented request.",
            actor=self.admin,
        )
        apply_due_date_revision(
            self.installment,
            revised_due_date=earlier,
            reason="Moving the target earlier at the student's request.",
            actor=self.admin,
        )

        self.assertEqual(effective_due_date(self.installment), earlier)
        self.assertEqual(self.installment.due_date_revisions.count(), 2)

    def test_revision_with_unchanged_date_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "matches the current effective"):
            apply_due_date_revision(
                self.installment,
                revised_due_date=self.original,
                reason=REASON,
                actor=self.admin,
            )

    def test_request_with_unchanged_date_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "matches the current effective"):
            self.make_request(proposed=self.original)

    def test_reason_shorter_than_ten_characters_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            apply_due_date_revision(
                self.installment,
                revised_due_date=date(2026, 8, 15),
                reason="short",
                actor=self.admin,
            )

        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            request_due_date_change(
                self.installment,
                proposed_due_date=date(2026, 8, 15),
                reason="   short   ",
                actor=self.admissions,
            )

    def test_second_pending_request_is_rejected_and_allowed_after_decision(self):
        first = self.make_request()

        with self.assertRaisesMessage(ValueError, "pending due-date change request"):
            self.make_request(proposed=date(2026, 9, 15))

        decide_due_date_change(first, approve=False, actor=self.admin)
        second = self.make_request(proposed=date(2026, 9, 15))

        self.assertEqual(second.status, DueDateChangeRequest.Status.PENDING)

    def test_decision_is_rejected_when_proposed_date_is_no_longer_a_change(self):
        change_request = self.make_request(proposed=date(2026, 8, 15))
        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 8, 15),
            reason="Applied directly while the request was pending.",
            actor=self.admin,
        )

        with self.assertRaisesMessage(ValueError, "nothing to apply"):
            decide_due_date_change(change_request, approve=True, actor=self.admin)

    def test_revision_only_touches_the_target_installment(self):
        sibling = self.course_installments(self.plan)[1]
        sibling_before = {
            "amount": sibling.amount,
            "original_due_date": sibling.original_due_date,
        }
        plan_before = {
            "course_fee": self.plan.course_fee,
            "gst_rate": self.plan.gst_rate,
            "gst_amount": self.plan.gst_amount,
            "total_payable": self.plan.total_payable,
        }
        target_before = {
            "amount": self.installment.amount,
            "original_due_date": self.installment.original_due_date,
        }

        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 12, 1),
            reason=REASON,
            actor=self.admin,
        )

        sibling.refresh_from_db()
        self.plan.refresh_from_db()
        self.installment.refresh_from_db()
        self.assertEqual(sibling.amount, sibling_before["amount"])
        self.assertEqual(sibling.original_due_date, sibling_before["original_due_date"])
        self.assertEqual(self.installment.amount, target_before["amount"])
        self.assertEqual(self.installment.original_due_date, target_before["original_due_date"])
        for field, value in plan_before.items():
            self.assertEqual(getattr(self.plan, field), value)
        self.assertEqual(
            sum((row.amount for row in self.plan.installments.all()), Decimal("0.00")),
            TOTAL_PAYABLE,
        )
        self.assertEqual(self.plan.gst_amount, GST_AMOUNT)

    def test_due_dates_cannot_be_revised_on_a_draft_plan(self):
        draft = self.make_draft_plan()
        installment = self.course_installments(draft)[0]

        with self.assertRaisesMessage(ValueError, "approved fee plan"):
            apply_due_date_revision(
                installment,
                revised_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=self.admin,
            )

        with self.assertRaisesMessage(ValueError, "approved fee plan"):
            request_due_date_change(
                installment,
                proposed_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=self.admissions,
            )

    def test_stage_based_charges_cannot_be_revised(self):
        application = self.plan.installments.get(category=FeeInstallment.Category.APPLICATION)

        with self.assertRaisesMessage(ValueError, "stage-based"):
            apply_due_date_revision(
                application,
                revised_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=self.admin,
            )

        with self.assertRaisesMessage(ValueError, "stage-based"):
            request_due_date_change(
                application,
                proposed_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=self.admissions,
            )

    def test_revision_from_a_foreign_centre_is_rejected(self):
        other_accounts = make_user("other_accounts", role="ACCOUNTS")
        other_accounts.centres.add(self.other_centre)

        with self.assertRaises(PermissionDenied):
            apply_due_date_revision(
                self.installment,
                revised_due_date=date(2026, 8, 15),
                reason=REASON,
                actor=other_accounts,
            )

    def test_approved_plan_status_is_unchanged_by_revisions(self):
        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 8, 15),
            reason=REASON,
            actor=self.admin,
        )

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, FeePlan.Status.APPROVED)
        self.assertEqual(self.plan.installments.count(), 6)

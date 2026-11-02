from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.fees.models import DueDateChangeRequest, FeeInstallment, FeePlan
from apps.fees.selectors import (
    admission_fee_summary,
    current_fee_plan,
    effective_due_date,
    installment_outstanding,
    pending_due_date_requests,
    plan_history,
)
from apps.fees.services import (
    apply_due_date_revision,
    approve_fee_plan,
    create_fee_plan_version,
    request_due_date_change,
    update_draft_fee_plan,
)
from apps.fees.tests.factories import (
    COURSE_DUE_DATES,
    TOTAL_PAYABLE,
    FeeTestBase,
)

REASON = "Documented student request to move the target date."


class EffectiveDueDateTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]

    def test_original_date_is_used_until_a_revision_is_applied(self):
        self.assertEqual(effective_due_date(self.installment), COURSE_DUE_DATES[0])

    def test_latest_applied_revision_wins(self):
        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 8, 15),
            reason=REASON,
            actor=self.admin,
        )
        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 9, 15),
            reason=REASON,
            actor=self.admin,
        )

        self.assertEqual(effective_due_date(self.installment), date(2026, 9, 15))

    def test_original_date_is_preserved(self):
        apply_due_date_revision(
            self.installment,
            revised_due_date=date(2026, 8, 15),
            reason=REASON,
            actor=self.admin,
        )
        self.installment.refresh_from_db()

        self.assertEqual(self.installment.original_due_date, COURSE_DUE_DATES[0])
        self.assertEqual(effective_due_date(self.installment), date(2026, 8, 15))

    def test_stage_based_charges_have_no_effective_due_date(self):
        application = self.plan.installments.get(category=FeeInstallment.Category.APPLICATION)

        self.assertIsNone(effective_due_date(application))


class PlanLookupTests(FeeTestBase):
    def test_current_plan_is_the_approved_version(self):
        plan = self.make_approved_plan()

        self.assertEqual(current_fee_plan(plan.admission), plan)

    def test_current_plan_is_none_without_an_approved_version(self):
        plan = self.make_draft_plan()

        self.assertIsNone(current_fee_plan(plan.admission))

    def test_plan_history_is_ordered_by_version(self):
        first = self.make_approved_plan()
        second = create_fee_plan_version(first, self.admin)

        self.assertEqual(list(plan_history(first.admission)), [first, second])

    def test_superseded_version_is_not_current(self):
        first = self.make_approved_plan()
        second = create_fee_plan_version(first, self.admin)
        approve_fee_plan(second, self.admin)

        self.assertEqual(current_fee_plan(first.admission), second)
        first.refresh_from_db()
        self.assertEqual(first.status, FeePlan.Status.SUPERSEDED)


class OutstandingTests(FeeTestBase):
    def test_outstanding_equals_amount_until_allocations_exist(self):
        plan = self.make_approved_plan()

        for installment in plan.installments.all():
            self.assertEqual(
                installment_outstanding(installment, date(2026, 7, 15)), installment.amount
            )


class AdmissionSummaryTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission

    def summary(self, as_of):
        return admission_fee_summary(self.admission, as_of)

    def test_no_plan_returns_zero_summary(self):
        admission = self.make_admission()
        summary = admission_fee_summary(admission, date(2026, 7, 15))

        self.assertEqual(summary.total_billed, Decimal("0.00"))
        self.assertEqual(summary.total_outstanding, Decimal("0.00"))
        self.assertEqual(summary.overdue_outstanding, Decimal("0.00"))
        self.assertEqual(summary.undated_outstanding, Decimal("0.00"))

    def test_billed_and_outstanding_use_the_approved_total(self):
        summary = self.summary(date(2026, 1, 1))

        self.assertEqual(summary.total_billed, TOTAL_PAYABLE)
        self.assertEqual(summary.total_paid, Decimal("0.00"))
        self.assertEqual(summary.total_outstanding, TOTAL_PAYABLE)

    def test_stage_based_charges_are_undated_and_never_overdue(self):
        summary = self.summary(date(2030, 1, 1))

        self.assertEqual(summary.undated_outstanding, Decimal("11000.00"))
        self.assertEqual(summary.overdue_outstanding, Decimal("186650.00"))

    def test_nothing_is_overdue_before_the_first_target(self):
        summary = self.summary(COURSE_DUE_DATES[0] - timedelta(days=1))

        self.assertEqual(summary.overdue_outstanding, Decimal("0.00"))
        self.assertEqual(summary.not_yet_due_outstanding, Decimal("186650.00"))

    def test_due_date_itself_is_not_overdue(self):
        summary = self.summary(COURSE_DUE_DATES[0])

        self.assertEqual(summary.overdue_outstanding, Decimal("0.00"))
        self.assertEqual(summary.not_yet_due_outstanding, Decimal("186650.00"))

    def test_day_after_due_date_is_overdue(self):
        summary = self.summary(COURSE_DUE_DATES[0] + timedelta(days=1))

        self.assertEqual(summary.overdue_outstanding, Decimal("40000.00"))
        self.assertEqual(summary.not_yet_due_outstanding, Decimal("146650.00"))
        self.assertEqual(summary.undated_outstanding, Decimal("11000.00"))

    def test_historical_as_of_reproduces_an_earlier_position(self):
        late = self.summary(date(2027, 12, 31))
        early = self.summary(date(2026, 7, 1))

        self.assertEqual(late.overdue_outstanding, Decimal("186650.00"))
        self.assertEqual(early.overdue_outstanding, Decimal("0.00"))
        self.assertEqual(late.total_outstanding, early.total_outstanding)

    def test_approved_extension_moves_an_installment_out_of_overdue(self):
        installment = self.course_installments(self.plan)[0]
        as_of = COURSE_DUE_DATES[0] + timedelta(days=10)
        self.assertEqual(self.summary(as_of).overdue_outstanding, Decimal("40000.00"))

        apply_due_date_revision(
            installment,
            revised_due_date=COURSE_DUE_DATES[0] + timedelta(days=60),
            reason=REASON,
            actor=self.admin,
        )

        self.assertEqual(self.summary(as_of).overdue_outstanding, Decimal("0.00"))
        self.assertEqual(self.summary(as_of).not_yet_due_outstanding, Decimal("186650.00"))

    def test_pending_request_does_not_move_an_installment_out_of_overdue(self):
        installment = self.course_installments(self.plan)[0]
        as_of = COURSE_DUE_DATES[0] + timedelta(days=10)
        request_due_date_change(
            installment,
            proposed_due_date=COURSE_DUE_DATES[0] + timedelta(days=60),
            reason=REASON,
            actor=self.admissions,
        )

        self.assertEqual(self.summary(as_of).overdue_outstanding, Decimal("40000.00"))

    def test_summary_uses_only_the_approved_plan(self):
        first = self.plan
        second = create_fee_plan_version(first, self.admin)
        components = [
            {
                "sequence": 1,
                "category": FeeInstallment.Category.APPLICATION,
                "description": "Application Fee",
                "amount": Decimal("1000.00"),
            },
            {
                "sequence": 2,
                "category": FeeInstallment.Category.ADMISSION,
                "description": "Admission Fee",
                "amount": Decimal("10000.00"),
            },
            {
                "sequence": 3,
                "category": FeeInstallment.Category.COURSE,
                "description": "Course 1",
                "amount": Decimal("27000.00"),
                "original_due_date": COURSE_DUE_DATES[0],
            },
            {
                "sequence": 4,
                "category": FeeInstallment.Category.COURSE,
                "description": "Course 2",
                "amount": Decimal("27000.00"),
                "original_due_date": COURSE_DUE_DATES[1],
            },
            {
                "sequence": 5,
                "category": FeeInstallment.Category.COURSE,
                "description": "Course 3",
                "amount": Decimal("27000.00"),
                "original_due_date": COURSE_DUE_DATES[2],
            },
            {
                "sequence": 6,
                "category": FeeInstallment.Category.COURSE,
                "description": "Course 4",
                "amount": Decimal("26000.00"),
                "original_due_date": COURSE_DUE_DATES[3],
            },
        ]
        update_draft_fee_plan(
            second,
            course_fee=Decimal("100000.00"),
            components=components,
            actor=self.admin,
        )
        approve_fee_plan(second, self.admin)

        summary = self.summary(date(2026, 1, 1))

        self.assertEqual(summary.total_billed, Decimal("118000.00"))
        self.assertEqual(summary.total_outstanding, Decimal("118000.00"))


class PendingRequestQueueTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]
        self.change_request = request_due_date_change(
            self.installment,
            proposed_due_date=date(2026, 8, 15),
            reason=REASON,
            actor=self.admissions,
        )

    def test_queue_lists_pending_requests_for_accessible_centres(self):
        self.assertEqual(list(pending_due_date_requests(self.accounts)), [self.change_request])
        self.assertEqual(list(pending_due_date_requests(self.admin)), [self.change_request])

    def test_queue_is_empty_for_a_user_without_centre_access(self):
        self.assertEqual(list(pending_due_date_requests(self.other_admissions)), [])

    def test_queue_can_be_narrowed_to_one_centre(self):
        self.assertEqual(
            list(pending_due_date_requests(self.admin, centre=self.centre)),
            [self.change_request],
        )
        self.assertEqual(
            list(pending_due_date_requests(self.admin, centre=self.other_centre)),
            [],
        )

    def test_decided_requests_leave_the_queue(self):
        self.change_request.status = DueDateChangeRequest.Status.REJECTED
        self.change_request.decided_by = self.admin
        self.change_request.decided_at = timezone.now()
        self.change_request.save(update_fields=["status", "decided_by", "decided_at"])

        self.assertEqual(list(pending_due_date_requests(self.admin)), [])

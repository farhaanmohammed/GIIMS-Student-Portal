from datetime import date

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.fees.models import DueDateChangeRequest, FeePlan
from apps.fees.services import (
    apply_due_date_revision,
    approve_fee_plan,
    cancel_draft_fee_plan,
    create_draft_fee_plan,
    create_fee_plan_version,
    decide_due_date_change,
    request_due_date_change,
)
from apps.fees.tests.factories import (
    COURSE_FEE,
    GST_RATE,
    FeeTestBase,
    default_components,
    make_user,
)

REASON = "Documented operational reason for the due-date change."


class ServiceRoleMatrixTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.admission = self.make_admission()
        self.approved_plan = self.make_approved_plan(admission=self.admission)
        self.installment = self.course_installments(self.approved_plan)[0]

    def test_plan_editors_can_create_draft_plans(self):
        # A3: ACCOUNTS is a plan editor; ADMISSIONS is not.
        for actor in (self.admin, self.management, self.accounts):
            with self.subTest(role=actor.role):
                plan = create_draft_fee_plan(
                    self.make_admission(),
                    course_fee=COURSE_FEE,
                    gst_rate=GST_RATE,
                    components=default_components(),
                    actor=actor,
                )
                self.assertEqual(plan.status, FeePlan.Status.DRAFT)

        with self.subTest(role=self.admissions.role), self.assertRaises(PermissionDenied):
            create_draft_fee_plan(
                self.make_admission(),
                course_fee=COURSE_FEE,
                gst_rate=GST_RATE,
                components=default_components(),
                actor=self.admissions,
            )

    def test_only_admin_and_management_can_approve_plans(self):
        for actor in (self.admin, self.management):
            draft = self.make_draft_plan()
            with self.subTest(role=actor.role):
                self.assertEqual(approve_fee_plan(draft, actor).status, FeePlan.Status.APPROVED)

        for actor in (self.accounts, self.admissions):
            draft = self.make_draft_plan()
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                approve_fee_plan(draft, actor)

    def test_plan_editors_can_version_plans(self):
        # A3: ACCOUNTS may create a new draft version; ADMISSIONS may not.
        for actor in (self.admin, self.management, self.accounts):
            plan = self.make_approved_plan()
            with self.subTest(role=actor.role):
                new_version = create_fee_plan_version(plan, actor)
                self.assertEqual(new_version.status, FeePlan.Status.DRAFT)
                self.assertEqual(new_version.supersedes, plan)

        plan = self.make_approved_plan()
        with self.subTest(role=self.admissions.role), self.assertRaises(PermissionDenied):
            create_fee_plan_version(plan, self.admissions)

    def test_only_admin_and_management_can_approve_or_cancel_plans(self):
        # A3 split: editors draft, approvers approve and cancel.
        for actor in (self.accounts, self.admissions):
            draft = self.make_draft_plan()
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                approve_fee_plan(draft, actor)
            with (
                self.subTest(role=actor.role, action="cancel"),
                self.assertRaises(PermissionDenied),
            ):
                cancel_draft_fee_plan(draft, actor, "Attempting to cancel this draft.")

    def test_admin_management_and_accounts_can_apply_due_date_revisions(self):
        for index, actor in enumerate((self.admin, self.management, self.accounts)):
            installment = self.course_installments(self.approved_plan)[index]
            with self.subTest(role=actor.role):
                revision = apply_due_date_revision(
                    installment,
                    revised_due_date=date(2026, 9, 10 + index),
                    reason=REASON,
                    actor=actor,
                )
                self.assertEqual(revision.revised_by, actor)

        with self.assertRaises(PermissionDenied):
            apply_due_date_revision(
                self.course_installments(self.approved_plan)[3],
                revised_due_date=date(2026, 9, 20),
                reason=REASON,
                actor=self.admissions,
            )

    def test_every_centre_role_can_request_a_due_date_change(self):
        for index, actor in enumerate(
            (self.admin, self.management, self.accounts, self.admissions)
        ):
            installment = self.course_installments(self.approved_plan)[index]
            with self.subTest(role=actor.role):
                change_request = request_due_date_change(
                    installment,
                    proposed_due_date=date(2026, 9, 10 + index),
                    reason=REASON,
                    actor=actor,
                )
                self.assertEqual(change_request.status, DueDateChangeRequest.Status.PENDING)

    def test_only_privileged_roles_can_decide_requests(self):
        for index, actor in enumerate((self.admin, self.management, self.accounts)):
            installment = self.course_installments(self.approved_plan)[index]
            change_request = request_due_date_change(
                installment,
                proposed_due_date=date(2026, 9, 10 + index),
                reason=REASON,
                actor=self.admissions,
            )
            with self.subTest(role=actor.role):
                decided = decide_due_date_change(change_request, approve=False, actor=actor)
                self.assertEqual(decided.decided_by, actor)

        change_request = request_due_date_change(
            self.course_installments(self.approved_plan)[3],
            proposed_due_date=date(2026, 9, 20),
            reason=REASON,
            actor=self.admissions,
        )
        with self.assertRaises(PermissionDenied):
            decide_due_date_change(change_request, approve=True, actor=self.admissions)


class ViewAccessMatrixTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()

    def test_each_centre_role_can_view_the_plan_list(self):
        for actor in (self.admin, self.management, self.accounts, self.admissions):
            with self.subTest(role=actor.role):
                self.client.force_login(actor)
                response = self.client.get(reverse("fees:fee_plan_list"))
                self.assertEqual(response.status_code, 200)

    def test_each_centre_role_can_view_a_plan_detail(self):
        for actor in (self.admin, self.management, self.accounts, self.admissions):
            with self.subTest(role=actor.role):
                self.client.force_login(actor)
                response = self.client.get(reverse("fees:fee_plan_detail", args=[self.plan.pk]))
                self.assertEqual(response.status_code, 200)

    def test_state_changing_endpoints_reject_get(self):
        routes = [
            reverse("fees:fee_plan_approve", args=[self.plan.pk]),
            reverse("fees:fee_plan_cancel", args=[self.plan.pk]),
            reverse("fees:fee_plan_new_version", args=[self.plan.pk]),
        ]
        self.client.force_login(self.admin)

        for url in routes:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)


class CrossCentreScopingTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]
        self.other_accounts = make_user("other_accounts", role="ACCOUNTS")
        self.other_accounts.centres.add(self.other_centre)

    def test_cross_centre_plan_detail_returns_404(self):
        self.client.force_login(self.other_accounts)

        response = self.client.get(reverse("fees:fee_plan_detail", args=[self.plan.pk]))

        self.assertEqual(response.status_code, 404)

    def test_cross_centre_due_date_form_returns_404(self):
        self.client.force_login(self.other_accounts)

        response = self.client.get(reverse("fees:installment_due_date", args=[self.installment.pk]))

        self.assertEqual(response.status_code, 404)

    def test_cross_centre_decision_returns_404(self):
        change_request = DueDateChangeRequest.objects.create(
            installment=self.installment,
            requested_by=self.admissions,
            previous_due_date=self.installment.original_due_date,
            proposed_due_date=date(2026, 8, 15),
            reason=REASON,
        )
        self.client.force_login(self.other_accounts)

        response = self.client.post(
            reverse("fees:due_date_request_decide", args=[change_request.pk]),
            {"decision": "approve"},
        )

        self.assertEqual(response.status_code, 404)
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, DueDateChangeRequest.Status.PENDING)

    def test_cross_centre_queue_is_empty(self):
        DueDateChangeRequest.objects.create(
            installment=self.installment,
            requested_by=self.admissions,
            previous_due_date=self.installment.original_due_date,
            proposed_due_date=date(2026, 8, 15),
            reason=REASON,
        )
        self.client.force_login(self.other_accounts)

        response = self.client.get(reverse("fees:due_date_request_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["due_date_requests"]), [])

from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.academics.models import Batch
from apps.admissions.models import Admission
from apps.fees.models import DueDateChangeRequest, FeeInstallment, FeePlan
from apps.fees.selectors import effective_due_date
from apps.fees.tests.factories import FeeTestBase, make_student

REASON = "Documented request to revise the installment target date."


class FeeViewTestBase(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]

    def plan_post_data(self, **overrides) -> dict:
        data = {
            "course_fee": "167500.00",
            "gst_rate": "18.00",
            "notes": "",
            "application_description": "Application Fee",
            "application_amount": "1000.00",
            "admission_description": "Admission Fee",
            "admission_amount": "10000.00",
            "course_1_description": "Course Installment 1",
            "course_1_amount": "40000.00",
            "course_1_due_date": "2026-07-15",
            "course_2_description": "Course Installment 2",
            "course_2_amount": "50000.00",
            "course_2_due_date": "2026-10-15",
            "course_3_description": "Course Installment 3",
            "course_3_amount": "50000.00",
            "course_3_due_date": "2027-01-15",
            "course_4_description": "Course Installment 4",
            "course_4_amount": "46650.00",
            "course_4_due_date": "2027-04-15",
        }
        data.update(overrides)
        return data


class FeeAuthenticationTests(FeeViewTestBase):
    def test_anonymous_users_are_redirected_to_login_on_every_fee_route(self):
        routes = [
            reverse("fees:fee_plan_list"),
            reverse("fees:fee_plan_detail", args=[self.plan.pk]),
            reverse("fees:fee_plan_create") + f"?admission={self.plan.admission_id}",
            reverse("fees:installment_due_date", args=[self.installment.pk]),
            reverse("fees:due_date_request_list"),
        ]

        for url in routes:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)


class FeePlanListTests(FeeViewTestBase):
    def test_list_is_centre_scoped(self):
        other_batch = Batch.objects.create(
            centre=self.other_centre,
            course=self.course,
            academic_year=self.academic_year,
            code="OCT26",
            start_date=self.batch.start_date,
            end_date=self.batch.end_date,
            is_active=True,
        )
        other_admission = self.make_admission(
            centre=self.other_centre,
            batch=other_batch,
            student=make_student(first_name="Other", last_name="Person"),
        )
        other_plan = self.make_approved_plan(admission=other_admission)

        self.client.force_login(self.accounts)
        response = self.client.get(reverse("fees:fee_plan_list"))

        self.assertContains(response, self.plan.admission.student.get_full_name())
        self.assertNotContains(response, other_plan.admission.student.get_full_name())

    def test_list_filters_by_status(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("fees:fee_plan_list"), {"status": "DRAFT"})

        self.assertContains(response, f"v{draft.version}")
        self.assertEqual(list(response.context["fee_plans"]), [draft])

    def test_htmx_request_returns_the_partial_template(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("fees:fee_plan_list"), headers={"HX-Request": "true"})

        self.assertTemplateUsed(response, "fees/partials/_fee_plan_table.html")


class FeePlanDetailTests(FeeViewTestBase):
    def test_detail_shows_gst_snapshot_and_component_dates(self):
        self.client.force_login(self.accounts)

        response = self.client.get(reverse("fees:fee_plan_detail", args=[self.plan.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "167500.00")
        self.assertContains(response, "18.00")
        self.assertContains(response, "30150.00")
        self.assertContains(response, "197650.00")
        self.assertContains(response, "Stage-based")

    def test_cross_centre_detail_returns_404(self):
        self.client.force_login(self.other_admissions)

        response = self.client.get(reverse("fees:fee_plan_detail", args=[self.plan.pk]))

        self.assertEqual(response.status_code, 404)


class FeePlanCreateViewTests(FeeViewTestBase):
    def setUp(self):
        super().setUp()
        self.admission = self.make_admission()

    def test_admissions_role_cannot_open_the_create_form(self):
        self.client.force_login(self.admissions)

        response = self.client.get(
            reverse("fees:fee_plan_create"), {"admission": self.admission.pk}
        )

        self.assertEqual(response.status_code, 403)

    def test_accounts_role_cannot_open_the_create_form(self):
        self.client.force_login(self.accounts)

        response = self.client.get(
            reverse("fees:fee_plan_create"), {"admission": self.admission.pk}
        )

        self.assertEqual(response.status_code, 403)

    def test_management_role_can_open_the_create_form(self):
        self.client.force_login(self.management)

        response = self.client.get(
            reverse("fees:fee_plan_create"), {"admission": self.admission.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stage-based charges")

    def test_admin_can_create_a_draft(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("fees:fee_plan_create"),
            {"admission": self.admission.pk, **self.plan_post_data()},
        )

        plan = FeePlan.objects.get(admission=self.admission)
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[plan.pk]))
        self.assertEqual(plan.status, FeePlan.Status.DRAFT)
        self.assertEqual(plan.installments.count(), 6)
        self.assertEqual(plan.total_payable, Decimal("197650.00"))

    def test_create_form_rejects_a_component_sum_mismatch(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("fees:fee_plan_create"),
            {
                "admission": self.admission.pk,
                **self.plan_post_data(course_1_amount="41000.00"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "add up to the plan total")
        self.assertFalse(FeePlan.objects.filter(admission=self.admission).exists())

    def test_create_requires_an_enrolled_admission(self):
        enquired = self.make_admission(status=Admission.Status.ENQUIRED)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("fees:fee_plan_create"), {"admission": enquired.pk})

        self.assertRedirects(response, reverse("admissions:admission_detail", args=[enquired.pk]))
        self.assertFalse(FeePlan.objects.filter(admission=enquired).exists())


class DraftEditingViewTests(FeeViewTestBase):
    def test_draft_can_be_edited(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("fees:fee_plan_update", args=[draft.pk]),
            self.plan_post_data(notes="Updated by staff."),
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[draft.pk]))
        self.assertEqual(draft.notes, "Updated by staff.")
        self.assertEqual(draft.installments.count(), 6)

    def test_approved_plan_edit_redirects_without_changing_anything(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("fees:fee_plan_update", args=[self.plan.pk]))

        self.plan.refresh_from_db()
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[self.plan.pk]))
        self.assertEqual(self.plan.status, FeePlan.Status.APPROVED)
        self.assertEqual(self.plan.total_payable, Decimal("197650.00"))


class PlanLifecycleViewTests(FeeViewTestBase):
    def test_approve_requires_post(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("fees:fee_plan_approve", args=[self.plan.pk]))

        self.assertEqual(response.status_code, 405)

    def test_admin_approves_a_draft_over_post(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.admin)

        response = self.client.post(reverse("fees:fee_plan_approve", args=[draft.pk]))

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[draft.pk]))
        self.assertEqual(draft.status, FeePlan.Status.APPROVED)

    def test_accounts_cannot_approve(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.accounts)

        response = self.client.post(reverse("fees:fee_plan_approve", args=[draft.pk]))

        draft.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(draft.status, FeePlan.Status.DRAFT)

    def test_new_version_creates_a_draft(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("fees:fee_plan_new_version", args=[self.plan.pk]))

        new_plan = FeePlan.objects.get(admission=self.plan.admission, version=2)
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[new_plan.pk]))
        self.assertEqual(new_plan.status, FeePlan.Status.DRAFT)
        self.assertEqual(new_plan.supersedes, self.plan)

    def test_cancel_draft_records_the_reason(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("fees:fee_plan_cancel", args=[draft.pk]),
            {"reason": "Replaced by a corrected draft."},
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[draft.pk]))
        self.assertEqual(draft.status, FeePlan.Status.CANCELLED)
        self.assertIn("Replaced by a corrected draft.", draft.notes)

    def test_cancel_without_a_valid_reason_leaves_the_draft_intact(self):
        draft = self.make_draft_plan()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("fees:fee_plan_cancel", args=[draft.pk]), {"reason": "short"}
        )

        draft.refresh_from_db()
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[draft.pk]))
        self.assertEqual(draft.status, FeePlan.Status.DRAFT)


class InstallmentDueDateViewTests(FeeViewTestBase):
    def test_admissions_role_sees_the_request_mode(self):
        self.client.force_login(self.admissions)
        response = self.client.get(reverse("fees:installment_due_date", args=[self.installment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Submit request")

    def test_accounts_role_sees_the_apply_mode(self):
        self.client.force_login(self.accounts)
        response = self.client.get(reverse("fees:installment_due_date", args=[self.installment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apply revision")

    def test_admissions_post_creates_a_pending_request(self):
        self.client.force_login(self.admissions)

        response = self.client.post(
            reverse("fees:installment_due_date", args=[self.installment.pk]),
            {"proposed_due_date": "2026-08-15", "reason": REASON},
        )

        change_request = DueDateChangeRequest.objects.get(installment=self.installment)
        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[self.plan.pk]))
        self.assertEqual(change_request.status, DueDateChangeRequest.Status.PENDING)
        self.assertFalse(self.installment.due_date_revisions.exists())

    def test_accounts_post_applies_the_revision_immediately(self):
        self.client.force_login(self.accounts)

        response = self.client.post(
            reverse("fees:installment_due_date", args=[self.installment.pk]),
            {"proposed_due_date": "2026-08-15", "reason": REASON},
        )

        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[self.plan.pk]))
        self.assertEqual(effective_due_date(self.installment), date(2026, 8, 15))
        self.assertFalse(DueDateChangeRequest.objects.filter(installment=self.installment).exists())

    def test_short_reason_is_rejected_by_the_form(self):
        self.client.force_login(self.admissions)

        response = self.client.post(
            reverse("fees:installment_due_date", args=[self.installment.pk]),
            {"proposed_due_date": "2026-08-15", "reason": "short"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DueDateChangeRequest.objects.filter(installment=self.installment).exists())

    def test_stage_based_charge_redirects_without_creating_a_request(self):
        application = self.plan.installments.get(category=FeeInstallment.Category.APPLICATION)
        self.client.force_login(self.admissions)

        response = self.client.get(reverse("fees:installment_due_date", args=[application.pk]))

        self.assertRedirects(response, reverse("fees:fee_plan_detail", args=[self.plan.pk]))
        self.assertFalse(DueDateChangeRequest.objects.filter(installment=application).exists())

    def test_cross_centre_installment_returns_404(self):
        self.client.force_login(self.other_admissions)

        response = self.client.get(reverse("fees:installment_due_date", args=[self.installment.pk]))

        self.assertEqual(response.status_code, 404)


class DueDateRequestQueueViewTests(FeeViewTestBase):
    def setUp(self):
        super().setUp()
        self.change_request = DueDateChangeRequest.objects.create(
            installment=self.installment,
            requested_by=self.admissions,
            previous_due_date=self.installment.original_due_date,
            proposed_due_date=date(2026, 8, 15),
            reason=REASON,
        )

    def test_admissions_role_cannot_open_the_approver_queue(self):
        self.client.force_login(self.admissions)

        response = self.client.get(reverse("fees:due_date_request_list"))

        self.assertEqual(response.status_code, 403)

    def test_approver_queue_lists_pending_requests(self):
        self.client.force_login(self.accounts)

        response = self.client.get(reverse("fees:due_date_request_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.installment.description)
        self.assertContains(response, "Approve")

    def test_decide_requires_post(self):
        self.client.force_login(self.accounts)

        response = self.client.get(
            reverse("fees:due_date_request_decide", args=[self.change_request.pk])
        )

        self.assertEqual(response.status_code, 405)

    def test_approve_creates_the_revision(self):
        self.client.force_login(self.accounts)

        response = self.client.post(
            reverse("fees:due_date_request_decide", args=[self.change_request.pk]),
            {"decision": "approve", "note": "Approved."},
        )

        self.change_request.refresh_from_db()
        self.assertRedirects(response, reverse("fees:due_date_request_list"))
        self.assertEqual(self.change_request.status, DueDateChangeRequest.Status.APPROVED)
        self.assertEqual(effective_due_date(self.installment), date(2026, 8, 15))

    def test_reject_creates_no_revision(self):
        self.client.force_login(self.admin)

        self.client.post(
            reverse("fees:due_date_request_decide", args=[self.change_request.pk]),
            {"decision": "reject", "note": "Not justified."},
        )

        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, DueDateChangeRequest.Status.REJECTED)
        self.assertFalse(self.installment.due_date_revisions.exists())

    def test_admissions_role_cannot_decide(self):
        self.client.force_login(self.admissions)

        response = self.client.post(
            reverse("fees:due_date_request_decide", args=[self.change_request.pk]),
            {"decision": "approve"},
        )

        self.assertEqual(response.status_code, 403)
        self.change_request.refresh_from_db()
        self.assertEqual(self.change_request.status, DueDateChangeRequest.Status.PENDING)

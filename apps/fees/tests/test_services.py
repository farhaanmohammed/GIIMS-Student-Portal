from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from apps.admissions.models import Admission
from apps.fees.models import FeeInstallment, FeePlan
from apps.fees.selectors import current_fee_plan
from apps.fees.services import (
    FeeComponent,
    approve_fee_plan,
    cancel_draft_fee_plan,
    compute_gst_amounts,
    create_draft_fee_plan,
    create_fee_plan_version,
    update_draft_fee_plan,
    validate_components,
)
from apps.fees.tests.factories import (
    COURSE_FEE,
    GST_AMOUNT,
    GST_RATE,
    TOTAL_PAYABLE,
    FeeTestBase,
    default_components,
)


class GstCalculationTests(SimpleTestCase):
    def test_eighteen_percent_on_taxable_value(self):
        course_fee, rate, gst_amount, total = compute_gst_amounts(
            Decimal("167500.00"), Decimal("18.00")
        )

        self.assertEqual(course_fee, Decimal("167500.00"))
        self.assertEqual(rate, Decimal("18.00"))
        self.assertEqual(gst_amount, Decimal("30150.00"))
        self.assertEqual(total, Decimal("197650.00"))

    def test_rounding_is_half_up_to_two_places(self):
        _, _, gst_amount, total = compute_gst_amounts(Decimal("100.05"), Decimal("18.00"))

        self.assertEqual(gst_amount, Decimal("18.01"))
        self.assertEqual(total, Decimal("118.06"))

    def test_zero_rate_is_allowed_and_produces_no_gst(self):
        _, _, gst_amount, total = compute_gst_amounts(Decimal("1000.00"), Decimal("0.00"))

        self.assertEqual(gst_amount, Decimal("0.00"))
        self.assertEqual(total, Decimal("1000.00"))

    def test_rate_outside_range_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_gst_amounts(Decimal("1000.00"), Decimal("101.00"))
        with self.assertRaises(ValueError):
            compute_gst_amounts(Decimal("1000.00"), Decimal("-0.01"))


class CreateDraftFeePlanTests(FeeTestBase):
    def test_creation_snapshots_gst_and_total(self):
        plan = self.make_draft_plan()

        self.assertEqual(plan.status, FeePlan.Status.DRAFT)
        self.assertEqual(plan.version, 1)
        self.assertEqual(plan.course_fee, COURSE_FEE)
        self.assertEqual(plan.gst_rate, GST_RATE)
        self.assertEqual(plan.gst_amount, GST_AMOUNT)
        self.assertEqual(plan.total_payable, TOTAL_PAYABLE)
        self.assertEqual(plan.currency, "INR")

    def test_creation_builds_six_components_in_order(self):
        plan = self.make_draft_plan()

        installments = list(plan.installments.order_by("sequence"))
        self.assertEqual(len(installments), 6)
        self.assertEqual([row.sequence for row in installments], [1, 2, 3, 4, 5, 6])
        self.assertEqual(
            [row.category for row in installments],
            [
                FeeInstallment.Category.APPLICATION,
                FeeInstallment.Category.ADMISSION,
                FeeInstallment.Category.COURSE,
                FeeInstallment.Category.COURSE,
                FeeInstallment.Category.COURSE,
                FeeInstallment.Category.COURSE,
            ],
        )
        self.assertIsNone(installments[0].original_due_date)
        self.assertIsNone(installments[1].original_due_date)
        for course_row in installments[2:]:
            self.assertIsNotNone(course_row.original_due_date)

    def test_components_sum_to_total_payable(self):
        plan = self.make_draft_plan()

        component_total = sum((row.amount for row in plan.installments.all()), Decimal("0.00"))

        self.assertEqual(component_total, plan.total_payable)
        self.assertEqual(
            component_total,
            Decimal("197650.00"),
            "Application and admission charges must sit inside the total, not on top of it.",
        )

    def test_creation_rejects_non_enrolled_admission(self):
        admission = self.make_admission(status=Admission.Status.ENQUIRED)

        with self.assertRaisesMessage(ValueError, "enrolled admission"):
            self.make_draft_plan(admission=admission)

    def test_creation_rejects_second_draft(self):
        admission = self.make_admission()
        self.make_draft_plan(admission=admission)

        with self.assertRaisesMessage(ValueError, "draft fee plan already exists"):
            self.make_draft_plan(admission=admission)

    def test_creation_rejects_new_draft_when_approved_plan_exists(self):
        admission = self.make_admission()
        self.make_approved_plan(admission=admission)

        with self.assertRaisesMessage(ValueError, "already has an approved fee plan"):
            self.make_draft_plan(admission=admission)

    def test_creation_requires_a_plan_editor_role(self):
        # A3: ACCOUNTS may draft fee plans; ADMISSIONS may not.
        admission = self.make_admission()
        plan = create_draft_fee_plan(
            admission,
            course_fee=COURSE_FEE,
            gst_rate=GST_RATE,
            components=default_components(),
            actor=self.accounts,
        )
        self.assertEqual(plan.status, FeePlan.Status.DRAFT)
        self.assertFalse(plan.approved_by_id)

        with self.assertRaises(PermissionDenied):
            create_draft_fee_plan(
                self.make_admission(),
                course_fee=COURSE_FEE,
                gst_rate=GST_RATE,
                components=default_components(),
                actor=self.admissions,
            )

    def test_creation_rejects_wrong_component_count(self):
        components = default_components()[:-1]

        with self.assertRaisesMessage(ValueError, "exactly six"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_duplicate_stage_component(self):
        components = default_components()
        components[1]["category"] = FeeInstallment.Category.APPLICATION
        components[1]["original_due_date"] = None

        with self.assertRaisesMessage(ValueError, "exactly one application"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_component_sum_mismatch(self):
        components = default_components()
        components[0]["amount"] = Decimal("1001.00")

        with self.assertRaisesMessage(ValueError, "add up to the plan total"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_stage_charge_with_date(self):
        components = default_components()
        components[0]["original_due_date"] = components[2]["original_due_date"]

        with self.assertRaisesMessage(ValueError, "stage-based"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_course_installment_without_date(self):
        components = default_components()
        components[2]["original_due_date"] = None

        with self.assertRaisesMessage(ValueError, "suggested due date"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_sequence_gap(self):
        components = default_components()
        components[-1]["sequence"] = 7

        with self.assertRaisesMessage(ValueError, "contiguous"):
            self.make_draft_plan(components=components)

    def test_creation_rejects_non_positive_component_amount(self):
        components = default_components()
        components[0]["amount"] = Decimal("0.00")

        with self.assertRaisesMessage(ValueError, "greater than zero"):
            self.make_draft_plan(components=components)


class UpdateDraftFeePlanTests(FeeTestBase):
    def test_update_recomputes_gst_and_replaces_components(self):
        plan = self.make_draft_plan()
        components = default_components()
        components[0]["amount"] = Decimal("2000.00")
        components[5]["amount"] = Decimal("45650.00")

        updated = update_draft_fee_plan(
            plan,
            course_fee=Decimal("167500.00"),
            gst_rate=Decimal("18.00"),
            components=components,
            notes="Revised split",
            actor=self.admin,
        )

        self.assertEqual(updated.total_payable, TOTAL_PAYABLE)
        self.assertEqual(updated.notes, "Revised split")
        self.assertEqual(updated.installments.count(), 6)
        self.assertEqual(updated.installments.get(sequence=1).amount, Decimal("2000.00"))

    def test_update_without_components_rejects_a_total_change(self):
        plan = self.make_draft_plan()

        with self.assertRaisesMessage(ValueError, "add up to the plan total"):
            update_draft_fee_plan(
                plan,
                course_fee=Decimal("170000.00"),
                actor=self.admin,
            )

    def test_approved_plan_cannot_be_edited(self):
        plan = self.make_approved_plan()
        original_notes = plan.notes

        with self.assertRaisesMessage(ValueError, "Only draft fee plans can be edited"):
            update_draft_fee_plan(
                plan,
                course_fee=Decimal("1.00"),
                components=default_components(),
                notes="Should not persist",
                actor=self.admin,
            )

        plan.refresh_from_db()
        self.assertEqual(plan.course_fee, COURSE_FEE)
        self.assertEqual(plan.gst_amount, GST_AMOUNT)
        self.assertEqual(plan.total_payable, TOTAL_PAYABLE)
        self.assertEqual(plan.notes, original_notes)
        self.assertEqual(plan.installments.count(), 6)

    def test_cancelled_plan_cannot_be_edited(self):
        plan = self.make_draft_plan()
        cancel_draft_fee_plan(plan, self.admin, "Draft abandoned before use.")

        with self.assertRaises(ValueError):
            update_draft_fee_plan(plan, notes="Nope", actor=self.admin)

    def test_update_requires_a_plan_editor_role(self):
        # A3: ACCOUNTS may edit drafts; ADMISSIONS may not.
        plan = self.make_draft_plan()
        updated = update_draft_fee_plan(plan, notes="Accounts edit", actor=self.accounts)
        self.assertEqual(updated.notes, "Accounts edit")

        with self.assertRaises(PermissionDenied):
            update_draft_fee_plan(plan, notes="Nope", actor=self.admissions)


class ApproveFeePlanTests(FeeTestBase):
    def test_approve_sets_status_actor_and_timestamp(self):
        plan = self.make_draft_plan()

        approved = approve_fee_plan(plan, self.management)

        self.assertEqual(approved.status, FeePlan.Status.APPROVED)
        self.assertEqual(approved.approved_by, self.management)
        self.assertIsNotNone(approved.approved_at)

    def test_approve_revalidates_component_sum(self):
        plan = self.make_draft_plan()
        plan.installments.filter(sequence=6).delete()

        with self.assertRaisesMessage(ValueError, "exactly six"):
            approve_fee_plan(plan, self.admin)

    def test_approved_plan_cannot_be_approved_again(self):
        plan = self.make_approved_plan()

        with self.assertRaisesMessage(ValueError, "Only draft fee plans can be approved"):
            approve_fee_plan(plan, self.admin)

    def test_accounts_and_admissions_cannot_approve(self):
        plan = self.make_draft_plan()

        for actor in (self.accounts, self.admissions):
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                approve_fee_plan(plan, actor)


class FeePlanVersionTests(FeeTestBase):
    def test_new_version_copies_amounts_and_components(self):
        original = self.make_approved_plan()

        new_version = create_fee_plan_version(original, self.admin)

        self.assertEqual(new_version.version, 2)
        self.assertEqual(new_version.status, FeePlan.Status.DRAFT)
        self.assertEqual(new_version.supersedes, original)
        self.assertEqual(new_version.course_fee, original.course_fee)
        self.assertEqual(new_version.gst_rate, original.gst_rate)
        self.assertEqual(new_version.total_payable, original.total_payable)
        self.assertEqual(
            [
                (row.sequence, row.category, row.amount, row.original_due_date)
                for row in new_version.installments.order_by("sequence")
            ],
            [
                (row.sequence, row.category, row.amount, row.original_due_date)
                for row in original.installments.order_by("sequence")
            ],
        )

    def test_unapproved_source_cannot_be_versioned(self):
        plan = self.make_draft_plan()

        with self.assertRaisesMessage(ValueError, "Only an approved fee plan"):
            create_fee_plan_version(plan, self.admin)

    def test_second_draft_version_is_rejected(self):
        original = self.make_approved_plan()
        create_fee_plan_version(original, self.admin)

        with self.assertRaisesMessage(ValueError, "draft version already exists"):
            create_fee_plan_version(original, self.admin)

    def test_approving_new_version_supersedes_the_old_one(self):
        original = self.make_approved_plan()
        original_rows = list(
            original.installments.order_by("sequence").values_list(
                "pk", "amount", "original_due_date"
            )
        )
        new_version = create_fee_plan_version(original, self.admin)

        approve_fee_plan(new_version, self.admin)

        original.refresh_from_db()
        new_version.refresh_from_db()
        self.assertEqual(original.status, FeePlan.Status.SUPERSEDED)
        self.assertEqual(new_version.status, FeePlan.Status.APPROVED)
        self.assertEqual(
            list(
                original.installments.order_by("sequence").values_list(
                    "pk", "amount", "original_due_date"
                )
            ),
            original_rows,
        )
        self.assertEqual(
            FeePlan.objects.filter(
                admission=original.admission, status=FeePlan.Status.APPROVED
            ).count(),
            1,
        )
        self.assertEqual(current_fee_plan(original.admission), new_version)

    def test_old_version_amounts_are_unchanged_by_the_new_version(self):
        original = self.make_approved_plan()
        before = {
            "course_fee": original.course_fee,
            "gst_rate": original.gst_rate,
            "gst_amount": original.gst_amount,
            "total_payable": original.total_payable,
        }

        new_version = create_fee_plan_version(original, self.admin)
        new_version.notes = "Edited on the new draft only."
        new_version.save(update_fields=["notes", "updated_at"])

        original.refresh_from_db()
        for field, value in before.items():
            self.assertEqual(getattr(original, field), value)


class CancelDraftFeePlanTests(FeeTestBase):
    def test_cancel_marks_draft_cancelled_and_records_reason(self):
        plan = self.make_draft_plan()

        cancelled = cancel_draft_fee_plan(plan, self.admin, "Superseded by a corrected draft.")

        self.assertEqual(cancelled.status, FeePlan.Status.CANCELLED)
        self.assertIn("Superseded by a corrected draft.", cancelled.notes)

    def test_cancel_rejects_short_reason(self):
        plan = self.make_draft_plan()

        with self.assertRaisesMessage(ValueError, "at least 10 characters"):
            cancel_draft_fee_plan(plan, self.admin, "too short")

    def test_accounts_and_admissions_cannot_cancel(self):
        # A3: cancelling stays with the approver roles, not the editors.
        for actor in (self.accounts, self.admissions):
            plan = self.make_draft_plan()
            with self.subTest(role=actor.role), self.assertRaises(PermissionDenied):
                cancel_draft_fee_plan(plan, actor, "Attempting to cancel this draft.")
            plan.refresh_from_db()
            self.assertEqual(plan.status, FeePlan.Status.DRAFT)

    def test_approved_plan_cannot_be_cancelled(self):
        plan = self.make_approved_plan()

        with self.assertRaisesMessage(ValueError, "Only draft fee plans can be cancelled"):
            cancel_draft_fee_plan(plan, self.admin, "Attempting to cancel an obligation.")


class ComponentValidationUnitTests(SimpleTestCase):
    def test_stage_components_inside_total_are_accepted(self):
        rows = [FeeComponent(**row) for row in default_components()]

        validate_components(rows, TOTAL_PAYABLE)

from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.fees.models import (
    DueDateChangeRequest,
    FeeInstallment,
    FeePlan,
    InstallmentDueDateRevision,
)
from apps.fees.tests.factories import FeeTestBase


class FeePlanConstraintTests(FeeTestBase):
    def test_duplicate_version_per_admission_is_rejected(self):
        admission = self.make_admission()
        self.make_plan_row(admission=admission, version=1)

        with self.assertRaises(IntegrityError):
            self.make_plan_row(admission=admission, version=1)

    def test_second_approved_plan_for_one_admission_is_rejected(self):
        admission = self.make_admission()
        self.make_plan_row(admission=admission, version=1, status=FeePlan.Status.APPROVED)

        with self.assertRaises(IntegrityError):
            self.make_plan_row(admission=admission, version=2, status=FeePlan.Status.APPROVED)

    def test_cancelled_draft_does_not_require_approval_metadata(self):
        plan = self.make_plan_row(
            status=FeePlan.Status.CANCELLED, notes="abandoned before approval"
        )

        self.assertEqual(plan.status, FeePlan.Status.CANCELLED)
        self.assertIsNone(plan.approved_at)

    def test_superseded_version_requires_approval_metadata(self):
        with self.assertRaises(IntegrityError):
            FeePlan.objects.create(
                admission=self.make_admission(),
                version=1,
                status=FeePlan.Status.SUPERSEDED,
                course_fee=Decimal("0.00"),
                gst_rate=Decimal("18.00"),
                gst_amount=Decimal("0.00"),
                total_payable=Decimal("0.00"),
            )

    def test_negative_course_fee_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_plan_row(
                course_fee=Decimal("-1.00"),
                gst_amount=Decimal("0.00"),
                total_payable=Decimal("-1.00"),
            )

    def test_gst_rate_above_one_hundred_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_plan_row(gst_rate=Decimal("101.00"))

    def test_gst_rate_below_zero_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_plan_row(gst_rate=Decimal("-0.01"))

    def test_total_must_equal_course_fee_plus_gst(self):
        with self.assertRaises(IntegrityError):
            self.make_plan_row(
                course_fee=Decimal("100.00"),
                gst_rate=Decimal("18.00"),
                gst_amount=Decimal("18.00"),
                total_payable=Decimal("999.00"),
            )

    def test_approved_plan_without_metadata_is_rejected(self):
        with self.assertRaises(IntegrityError):
            FeePlan.objects.create(
                admission=self.make_admission(),
                version=1,
                status=FeePlan.Status.APPROVED,
                course_fee=Decimal("0.00"),
                gst_rate=Decimal("18.00"),
                gst_amount=Decimal("0.00"),
                total_payable=Decimal("0.00"),
            )


class FeeInstallmentConstraintTests(FeeTestBase):
    def test_duplicate_sequence_within_a_plan_is_rejected(self):
        plan = self.make_plan_row()
        FeeInstallment.objects.create(
            fee_plan=plan,
            sequence=1,
            category=FeeInstallment.Category.COURSE,
            description="First",
            amount=Decimal("10.00"),
            original_due_date=date(2026, 7, 15),
        )

        with self.assertRaises(IntegrityError):
            FeeInstallment.objects.create(
                fee_plan=plan,
                sequence=1,
                category=FeeInstallment.Category.APPLICATION,
                description="Duplicate",
                amount=Decimal("10.00"),
            )

    def test_course_installment_without_due_date_is_rejected(self):
        plan = self.make_plan_row()

        with self.assertRaises(IntegrityError):
            FeeInstallment.objects.create(
                fee_plan=plan,
                sequence=1,
                category=FeeInstallment.Category.COURSE,
                description="Undated course",
                amount=Decimal("10.00"),
            )

    def test_stage_based_charge_with_due_date_is_rejected(self):
        plan = self.make_plan_row()

        with self.assertRaises(IntegrityError):
            FeeInstallment.objects.create(
                fee_plan=plan,
                sequence=1,
                category=FeeInstallment.Category.APPLICATION,
                description="Application",
                amount=Decimal("10.00"),
                original_due_date=date(2026, 7, 15),
            )

    def test_zero_or_negative_amount_is_rejected(self):
        plan = self.make_plan_row()

        with self.assertRaises(IntegrityError):
            FeeInstallment.objects.create(
                fee_plan=plan,
                sequence=1,
                category=FeeInstallment.Category.APPLICATION,
                description="Application",
                amount=Decimal("0.00"),
            )


class DueDateAuditConstraintTests(FeeTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.installment = self.course_installments(self.plan)[0]

    def make_request(self, **overrides):
        data = {
            "installment": self.installment,
            "requested_by": self.admissions,
            "previous_due_date": self.installment.original_due_date,
            "proposed_due_date": date(2026, 8, 15),
            "reason": "Student requested a revised schedule for family reasons.",
        }
        data.update(overrides)
        return DueDateChangeRequest.objects.create(**data)

    def test_revision_with_unchanged_date_is_rejected(self):
        with self.assertRaises(IntegrityError):
            InstallmentDueDateRevision.objects.create(
                installment=self.installment,
                previous_due_date=self.installment.original_due_date,
                revised_due_date=self.installment.original_due_date,
                reason="No actual change was made here.",
                revised_by=self.admin,
            )

    def test_request_with_unchanged_date_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_request(proposed_due_date=self.installment.original_due_date)

    def test_two_pending_requests_for_one_installment_are_rejected(self):
        self.make_request()

        with self.assertRaises(IntegrityError):
            self.make_request(proposed_due_date=date(2026, 9, 15))

    def test_decided_request_without_decider_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_request(status=DueDateChangeRequest.Status.APPROVED)

    def test_pending_request_with_decider_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.make_request(decided_by=self.admin, decided_at=timezone.now())

    def test_revision_is_append_only(self):
        revision = InstallmentDueDateRevision.objects.create(
            installment=self.installment,
            previous_due_date=self.installment.original_due_date,
            revised_due_date=date(2026, 8, 15),
            reason="Student requested a revised schedule for family reasons.",
            revised_by=self.admin,
        )

        revision.reason = "Rewritten audit text"
        with self.assertRaises(ValueError):
            revision.save()

        with self.assertRaises(ValueError):
            revision.delete()

    def test_cancelled_request_outside_pending_uniqueness_allows_new_request(self):
        first = self.make_request()
        first.status = DueDateChangeRequest.Status.CANCELLED
        first.decided_by = self.admin
        first.decided_at = timezone.now()
        first.save(update_fields=["status", "decided_by", "decided_at"])

        second = self.make_request(proposed_due_date=date(2026, 9, 15))

        self.assertEqual(second.status, DueDateChangeRequest.Status.PENDING)


class PolicyAbsenceTests(TestCase):
    """No discount, waiver, concession, penalty, or late-fee fields exist."""

    def test_fee_models_expose_no_discount_or_penalty_fields(self):
        forbidden = ("discount", "waiver", "concession", "scholarship", "penalty", "late_fee")

        for model in (FeePlan, FeeInstallment, InstallmentDueDateRevision, DueDateChangeRequest):
            field_names = {field.name.lower() for field in model._meta.get_fields()}
            for term in forbidden:
                matching = {name for name in field_names if term in name}
                self.assertFalse(matching, f"{model.__name__} unexpectedly exposes {matching}")

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel


class FeePlan(TimeStampedModel):
    """
    A versioned fee structure for a single admission.

    Amounts, the GST rate, and the calculated GST are snapshotted here when the
    plan is created and become immutable once the plan is approved. There is no
    global GST setting and no automatic course default: each plan records the
    rate that applied to it.

    ``FeePlan`` deliberately has no ``centre`` column. Centre scoping resolves
    through ``admission__centre`` so a cross-centre plan behaves like a
    non-existent one.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        CANCELLED = "CANCELLED", "Cancelled"

    admission = models.ForeignKey(
        "admissions.Admission",
        on_delete=models.PROTECT,
        related_name="fee_plans",
        help_text="Admission this fee structure belongs to.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number within the admission, starting at 1.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        help_text="Draft plans are editable; approved plans are immutable.",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
        help_text="The earlier approved version this correction replaces.",
    )
    currency = models.CharField(max_length=3, default="INR")

    course_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Taxable course value, exclusive of GST.",
    )
    gst_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="GST rate snapshotted for this plan (e.g. 18.00).",
    )
    gst_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="GST amount computed by the service, never entered by a user.",
    )
    total_payable = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="course_fee + gst_amount. Installments must sum to this value.",
    )
    notes = models.TextField(blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_fee_plans",
    )

    class Meta:
        ordering = ("admission", "version")
        verbose_name = "Fee Plan"
        verbose_name_plural = "Fee Plans"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(course_fee__gte=0),
                name="fee_plan_course_fee_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(gst_amount__gte=0),
                name="fee_plan_gst_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_payable__gte=0),
                name="fee_plan_total_payable_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(gst_rate__gte=0) & Q(gst_rate__lte=100),
                name="fee_plan_gst_rate_range",
            ),
            models.CheckConstraint(
                condition=Q(total_payable=F("course_fee") + F("gst_amount")),
                name="fee_plan_total_matches_components",
            ),
            # Drafts and cancelled drafts have no approval metadata. Approved
            # and superseded versions must always record who approved them and
            # when.
            models.CheckConstraint(
                condition=(
                    Q(status__in=["DRAFT", "CANCELLED"])
                    | (Q(approved_at__isnull=False) & Q(approved_by__isnull=False))
                ),
                name="fee_plan_approved_requires_metadata",
            ),
            models.UniqueConstraint(
                fields=["admission", "version"],
                name="fee_plan_unique_version_per_admission",
            ),
            models.UniqueConstraint(
                fields=["admission"],
                condition=Q(status="APPROVED"),
                name="fee_plan_one_approved_per_admission",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.admission} - fee plan v{self.version} ({self.status})"


class FeeInstallment(TimeStampedModel):
    """
    One component of a fee plan.

    ``APPLICATION`` and ``ADMISSION`` components are stage-based and carry no
    calendar date. The four ``COURSE`` components each carry an original
    suggested due date. ``original_due_date`` is never rewritten; student
    specific changes are stored as ``InstallmentDueDateRevision`` rows.
    """

    class Category(models.TextChoices):
        APPLICATION = "APPLICATION", "Application"
        ADMISSION = "ADMISSION", "Admission"
        COURSE = "COURSE", "Course"

    fee_plan = models.ForeignKey(
        FeePlan,
        on_delete=models.PROTECT,
        related_name="installments",
    )
    sequence = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    original_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Suggested due date. Always blank for application and admission charges.",
    )

    class Meta:
        ordering = ("fee_plan", "sequence")
        verbose_name = "Fee Installment"
        verbose_name_plural = "Fee Installments"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["fee_plan", "sequence"],
                name="fee_installment_unique_sequence",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="fee_installment_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(category="COURSE", original_due_date__isnull=False)
                    | Q(
                        category__in=["APPLICATION", "ADMISSION"],
                        original_due_date__isnull=True,
                    )
                ),
                name="fee_installment_due_date_matches_category",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fee_plan} - {self.sequence}. {self.description}"


class InstallmentDueDateRevision(models.Model):
    """
    Append-only audit row for an applied student-specific due-date change.

    Creating a revision never writes to ``FeePlan`` or ``FeeInstallment``.
    Effective due date is the latest revision, falling back to the original
    suggested date.
    """

    installment = models.ForeignKey(
        FeeInstallment,
        on_delete=models.PROTECT,
        related_name="due_date_revisions",
    )
    previous_due_date = models.DateField(null=True, blank=True)
    revised_due_date = models.DateField()
    reason = models.TextField(help_text="Required business reason, at least 10 characters.")
    revised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="applied_due_date_revisions",
        help_text="The user who applied the change.",
    )
    source_request = models.ForeignKey(
        "fees.DueDateChangeRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resulting_revisions",
        help_text="Set when the change came from an approved request.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "Installment Due-Date Revision"
        verbose_name_plural = "Installment Due-Date Revisions"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(previous_due_date__isnull=True)
                | ~Q(revised_due_date=F("previous_due_date")),
                name="due_date_revision_changes_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.installment} due {self.revised_due_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(
                "Installment due-date revisions are append-only and cannot be updated."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Installment due-date revisions are append-only and cannot be deleted.")


class DueDateChangeRequest(models.Model):
    """
    Workflow row for a requested due-date change.

    ``ADMISSIONS`` staff may create a ``PENDING`` request only.
    ``ADMIN``, ``MANAGEMENT``, and ``ACCOUNTS`` approve or reject it; approval
    creates an ``InstallmentDueDateRevision`` in the same transaction.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    installment = models.ForeignKey(
        FeeInstallment,
        on_delete=models.PROTECT,
        related_name="due_date_change_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="due_date_change_requests",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    previous_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Effective due date captured when the request was raised.",
    )
    proposed_due_date = models.DateField()
    reason = models.TextField(help_text="Required business reason, at least 10 characters.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_due_date_requests",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-requested_at", "-pk")
        verbose_name = "Due-Date Change Request"
        verbose_name_plural = "Due-Date Change Requests"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(previous_due_date__isnull=True)
                | ~Q(proposed_due_date=F("previous_due_date")),
                name="due_date_request_changes_date",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="PENDING", decided_by__isnull=True, decided_at__isnull=True)
                    | (
                        Q(status__in=["APPROVED", "REJECTED", "CANCELLED"])
                        & Q(decided_by__isnull=False)
                        & Q(decided_at__isnull=False)
                    )
                ),
                name="due_date_request_decision_metadata",
            ),
            models.UniqueConstraint(
                fields=["installment"],
                condition=Q(status="PENDING"),
                name="due_date_request_one_pending_per_installment",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.installment} {self.previous_due_date} -> "
            f"{self.proposed_due_date} ({self.status})"
        )

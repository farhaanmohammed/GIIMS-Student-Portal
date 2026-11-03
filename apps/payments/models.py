from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel

MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2


class PaymentMode(models.TextChoices):
    """How money was received. Operational, not a pricing policy."""

    CASH = "CASH", "Cash"
    UPI = "UPI", "UPI"
    CARD = "CARD", "Card"
    BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
    CHEQUE = "CHEQUE", "Cheque"
    DEMAND_DRAFT = "DEMAND_DRAFT", "Demand draft"
    OTHER = "OTHER", "Other"


class ReceiptSequence(TimeStampedModel):
    """
    Concurrency-safe receipt counter, one row per centre and financial year.

    The row is locked with ``select_for_update()`` while the next number is
    reserved. Sequence numbers are never reused: the counter only increases,
    so cancelled, reversed, and refunded receipts keep their original number.
    """

    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="receipt_sequences",
    )
    financial_year = models.CharField(
        max_length=7,
        help_text="Indian financial year label, for example FY26-27.",
    )
    last_sequence = models.PositiveIntegerField(
        default=0,
        help_text="Highest receipt sequence already issued for this centre and year.",
    )

    class Meta:
        verbose_name = "Receipt Sequence"
        verbose_name_plural = "Receipt Sequences"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(last_sequence__gte=0),
                name="receipt_sequence_non_negative",
            ),
            models.UniqueConstraint(
                fields=["centre", "financial_year"],
                name="receipt_sequence_unique_per_centre_year",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.centre.code} {self.financial_year}: {self.last_sequence}"


class Payment(TimeStampedModel):
    """
    Money received from a student for one admission.

    ``centre`` is the centre that actually collected the money. It may differ
    from ``admission.centre`` (the student's assigned centre); reports must
    never conflate the two.

    ``request_key`` is the durable idempotency key for the collection form.
    A retried submission with the same key returns the existing payment
    instead of collecting twice.
    """

    class Status(models.TextChoices):
        POSTED = "POSTED", "Posted"
        REVERSED = "REVERSED", "Reversed"

    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="collected_payments",
        help_text="Centre where the payment was physically received.",
    )
    admission = models.ForeignKey(
        "admissions.Admission",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        help_text="Total money received, including any amount retained as credit.",
    )
    currency = models.CharField(max_length=3, default="INR")
    paid_on = models.DateField(
        db_index=True,
        help_text="Actual date the payment was received (not the posting timestamp).",
    )
    mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Cheque number, UPI reference, or bank transaction reference.",
    )
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.POSTED,
        db_index=True,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_payments",
    )
    posted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    request_key = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        help_text="Idempotency key supplied by the collection form.",
    )

    class Meta:
        ordering = ("-paid_on", "-pk")
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["centre", "paid_on"], name="payment_centre_date_idx"),
            models.Index(fields=["admission", "paid_on"], name="payment_admission_date_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"Payment {self.pk} - {self.amount} {self.currency} on {self.paid_on}"


class Receipt(TimeStampedModel):
    """
    Traceable receipt for one collected payment.

    A receipt is never deleted, renumbered, or reused. Reversing the payment
    changes ``status`` and leaves the original record and number intact.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVERSED = "REVERSED", "Reversed"
        CANCELLED = "CANCELLED", "Cancelled"

    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name="receipt",
    )
    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    financial_year = models.CharField(max_length=7)
    sequence = models.PositiveIntegerField()
    number = models.CharField(
        max_length=60,
        unique=True,
        help_text="GIIMS/{CENTRE_CODE}/{FINANCIAL_YEAR}/{SEQUENCE}",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_receipts",
    )
    issued_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-issued_at", "-pk")
        verbose_name = "Receipt"
        verbose_name_plural = "Receipts"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["centre", "financial_year", "sequence"],
                name="receipt_centre_year_seq_idx",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(sequence__gt=0),
                name="receipt_sequence_positive",
            ),
            models.UniqueConstraint(
                fields=["centre", "financial_year", "sequence"],
                name="receipt_unique_centre_year_sequence",
            ),
        ]

    def __str__(self) -> str:
        return self.number


class AdvanceCredit(TimeStampedModel):
    """
    A lot of money held on a student's admission as advance credit.

    ``remaining_amount`` is stored for locking and reporting. It must always
    equal ``original_amount`` minus approved credit applications and refunds
    taken from this lot; every service that changes it runs inside a
    transaction with the lot locked. ``reconcile_credit_remaining`` in
    ``apps.payments.selectors`` exposes the derived figure for tests and
    reporting.

    A lot is created for an overpayment (linked to its payment) or for money
    released by an approved fee-plan reassignment.
    """

    class Source(models.TextChoices):
        OVERPAYMENT = "OVERPAYMENT", "Overpayment"
        REASSIGNMENT = "REASSIGNMENT", "Fee-plan reassignment"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DEPLETED = "DEPLETED", "Depleted"
        REVERSED = "REVERSED", "Reversed"

    admission = models.ForeignKey(
        "admissions.Admission",
        on_delete=models.PROTECT,
        related_name="advance_credits",
    )
    source_payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_lots",
        help_text="Payment whose excess created this lot, when applicable.",
    )
    source_type = models.CharField(max_length=20, choices=Source.choices)
    original_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    remaining_amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_advance_credits",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("created_at", "pk")
        verbose_name = "Advance Credit"
        verbose_name_plural = "Advance Credit"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["admission", "status"], name="credit_admission_status_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(original_amount__gt=0),
                name="credit_original_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(remaining_amount__gte=0),
                name="credit_remaining_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(remaining_amount__lte=F("original_amount")),
                name="credit_remaining_not_above_original",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_type="OVERPAYMENT", source_payment__isnull=False)
                    | Q(source_type="REASSIGNMENT", source_payment__isnull=True)
                ),
                name="credit_source_matches_payment",
            ),
            models.UniqueConstraint(
                fields=["source_payment"],
                condition=Q(source_payment__isnull=False),
                name="credit_one_lot_per_payment",
            ),
        ]

    def __str__(self) -> str:
        return f"Credit {self.pk} - {self.remaining_amount} of {self.original_amount}"


class InstallmentAllocation(TimeStampedModel):
    """
    One explicit amount applied to one installment.

    Allocations are separate rows from the payment so partial payments,
    multi-installment payments, and advance-credit applications are all
    individually traceable. Rows are never deleted: a reversal flips
    ``status`` and records the actor, timestamp, and reason.
    """

    class Source(models.TextChoices):
        PAYMENT = "PAYMENT", "Payment"
        ADVANCE_CREDIT = "ADVANCE_CREDIT", "Advance credit"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVERSED = "REVERSED", "Reversed"

    installment = models.ForeignKey(
        "fees.FeeInstallment",
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    source_type = models.CharField(max_length=20, choices=Source.choices)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocations",
    )
    credit = models.ForeignKey(
        AdvanceCredit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocations",
    )
    request_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Idempotency key for advance-credit applications.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="installment_allocations",
    )
    allocated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_allocations",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("installment", "allocated_at", "pk")
        verbose_name = "Installment Allocation"
        verbose_name_plural = "Installment Allocations"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["installment", "status"], name="alloc_installment_status_idx"),
            models.Index(fields=["payment", "status"], name="allocation_payment_status_idx"),
            models.Index(fields=["credit", "status"], name="allocation_credit_status_idx"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="allocation_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_type="PAYMENT", payment__isnull=False, credit__isnull=True)
                    | Q(source_type="ADVANCE_CREDIT", payment__isnull=True, credit__isnull=False)
                ),
                name="allocation_source_matches_links",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="ACTIVE", reversed_by__isnull=True, reversed_at__isnull=True)
                    | Q(status="REVERSED", reversed_by__isnull=False, reversed_at__isnull=False)
                ),
                name="allocation_reversal_metadata",
            ),
            models.UniqueConstraint(
                fields=["request_key", "credit"],
                condition=Q(source_type="ADVANCE_CREDIT", request_key__isnull=False),
                name="allocation_one_credit_use_per_request",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} to {self.installment} ({self.status})"


class Refund(TimeStampedModel):
    """
    Controlled refund of available advance credit.

    ``ADMIN``/``MANAGEMENT`` decide the request; approval is the only point
    at which credit is reduced. The original payment and receipt are never
    altered or deleted. A refund is a distinct financial transaction with
    its own requestor, approver, reason, mode, and actual refund date.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    admission = models.ForeignKey(
        "admissions.Admission",
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    credit = models.ForeignKey(
        AdvanceCredit,
        on_delete=models.PROTECT,
        related_name="refunds",
        help_text="Credit lot the refund is drawn from.",
    )
    original_payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="refunds",
        help_text="Original collection reference, when the credit came from a payment.",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    reason = models.TextField(help_text="Required business reason, at least 10 characters.")
    mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_refunds",
    )
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_refunds",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    refunded_on = models.DateField(
        null=True,
        blank=True,
        help_text="Actual refund date, recorded on approval.",
    )
    request_key = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ("-requested_at", "-pk")
        verbose_name = "Refund"
        verbose_name_plural = "Refunds"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="refund_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="PENDING",
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                        refunded_on__isnull=True,
                    )
                    | (
                        Q(status__in=["APPROVED", "REJECTED"])
                        & Q(decided_by__isnull=False)
                        & Q(decided_at__isnull=False)
                    )
                ),
                name="refund_decision_metadata",
            ),
            models.CheckConstraint(
                condition=~Q(status="APPROVED") | Q(refunded_on__isnull=False),
                name="refund_approved_requires_date",
            ),
        ]

    def __str__(self) -> str:
        return f"Refund {self.amount} ({self.status})"


class RefundCreditAllocation(models.Model):
    """Which credit lot funded a refund, and by how much. Append-only."""

    refund = models.ForeignKey(
        Refund,
        on_delete=models.PROTECT,
        related_name="credit_allocations",
    )
    credit = models.ForeignKey(
        AdvanceCredit,
        on_delete=models.PROTECT,
        related_name="refund_allocations",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("refund", "pk")
        verbose_name = "Refund Credit Allocation"
        verbose_name_plural = "Refund Credit Allocations"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="refund_credit_allocation_positive",
            ),
            models.UniqueConstraint(
                fields=["refund", "credit"],
                name="refund_unique_credit_lot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.refund} <- {self.credit} ({self.amount})"


class PaymentReversal(TimeStampedModel):
    """
    Authorised reversal of a posted payment.

    One row per payment, enforced by ``OneToOneField``, so the same payment
    cannot be reversed twice. Reversal never deletes the payment, receipt, or
    allocation history; it appends reversal metadata and compensating state.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
    )
    reason = models.TextField(help_text="Required business reason, at least 10 characters.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_payment_reversals",
    )
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_payment_reversals",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    request_key = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ("-requested_at", "-pk")
        verbose_name = "Payment Reversal"
        verbose_name_plural = "Payment Reversals"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_reversal_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="PENDING", decided_by__isnull=True, decided_at__isnull=True)
                    | (
                        Q(status__in=["APPROVED", "REJECTED"])
                        & Q(decided_by__isnull=False)
                        & Q(decided_at__isnull=False)
                    )
                ),
                name="payment_reversal_decision_metadata",
            ),
        ]

    def __str__(self) -> str:
        return f"Reversal of payment {self.payment_id} ({self.status})"


class FinancialAuditEvent(models.Model):
    """
    Append-only record of every financial event.

    Services write one row per meaningful action so the full history can be
    reconstructed without relying on mutable rows.
    """

    class Event(models.TextChoices):
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Payment recorded"
        RECEIPT_ISSUED = "RECEIPT_ISSUED", "Receipt issued"
        ALLOCATION_CREATED = "ALLOCATION_CREATED", "Allocation created"
        CREDIT_CREATED = "CREDIT_CREATED", "Advance credit created"
        CREDIT_APPLIED = "CREDIT_APPLIED", "Advance credit applied"
        REFUND_REQUESTED = "REFUND_REQUESTED", "Refund requested"
        REFUND_APPROVED = "REFUND_APPROVED", "Refund approved"
        REFUND_REJECTED = "REFUND_REJECTED", "Refund rejected"
        REVERSAL_REQUESTED = "REVERSAL_REQUESTED", "Reversal requested"
        REVERSAL_APPROVED = "REVERSAL_APPROVED", "Reversal approved"
        REVERSAL_REJECTED = "REVERSAL_REJECTED", "Reversal rejected"
        REASSIGNMENT_REQUESTED = "REASSIGNMENT_REQUESTED", "Reassignment requested"
        REASSIGNMENT_APPROVED = "REASSIGNMENT_APPROVED", "Reassignment approved"
        REASSIGNMENT_REJECTED = "REASSIGNMENT_REJECTED", "Reassignment rejected"

    admission = models.ForeignKey(
        "admissions.Admission",
        on_delete=models.PROTECT,
        related_name="financial_events",
    )
    event_type = models.CharField(max_length=40, choices=Event.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financial_events",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    installment = models.ForeignKey(
        "fees.FeeInstallment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "Financial Audit Event"
        verbose_name_plural = "Financial Audit Events"

    def __str__(self) -> str:
        return f"{self.event_type} on {self.admission_id} by {self.actor}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Financial audit events are append-only and cannot be updated.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Financial audit events are append-only and cannot be deleted.")

from django.contrib import admin

from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
    Payment,
    PaymentReversal,
    Receipt,
    ReceiptSequence,
    Refund,
    RefundCreditAllocation,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Payments are financial records: writes go through services only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "receipt_number",
        "admission",
        "centre",
        "amount",
        "paid_on",
        "mode",
        "status",
        "recorded_by",
    )
    list_filter = ("status", "mode", "centre")
    search_fields = (
        "admission__student__student_id",
        "admission__student__first_name",
        "admission__student__last_name",
        "receipt__number",
        "reference",
    )
    list_select_related = ("admission__student", "centre", "receipt", "recorded_by")

    @admin.display(description="Receipt")
    def receipt_number(self, obj):
        receipt = getattr(obj, "receipt", None)
        return receipt.number if receipt else "-"


@admin.register(Receipt)
class ReceiptAdmin(ReadOnlyAdmin):
    list_display = ("number", "centre", "financial_year", "sequence", "status", "issued_by")
    list_filter = ("status", "centre", "financial_year")
    search_fields = ("number", "payment__admission__student__student_id")
    list_select_related = ("centre", "payment", "issued_by")


@admin.register(ReceiptSequence)
class ReceiptSequenceAdmin(ReadOnlyAdmin):
    list_display = ("centre", "financial_year", "last_sequence")
    list_filter = ("centre", "financial_year")


@admin.register(InstallmentAllocation)
class InstallmentAllocationAdmin(ReadOnlyAdmin):
    list_display = (
        "installment",
        "amount",
        "source_type",
        "payment",
        "credit",
        "status",
        "allocated_by",
        "allocated_at",
    )
    list_filter = ("source_type", "status")
    search_fields = ("installment__fee_plan__admission__student__student_id",)
    list_select_related = ("installment", "payment", "credit", "allocated_by")


@admin.register(AdvanceCredit)
class AdvanceCreditAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "admission",
        "source_type",
        "original_amount",
        "remaining_amount",
        "status",
        "created_by",
    )
    list_filter = ("source_type", "status")
    search_fields = ("admission__student__student_id",)
    list_select_related = ("admission__student", "source_payment", "created_by")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "admission",
        "amount",
        "status",
        "mode",
        "requested_by",
        "decided_by",
        "refunded_on",
    )
    list_filter = ("status", "mode")
    search_fields = ("admission__student__student_id", "reason", "decision_note")
    list_select_related = ("admission__student", "credit", "requested_by", "decided_by")


@admin.register(RefundCreditAllocation)
class RefundCreditAllocationAdmin(ReadOnlyAdmin):
    list_display = ("refund", "credit", "amount", "created_at")
    list_select_related = ("refund", "credit")


@admin.register(PaymentReversal)
class PaymentReversalAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "payment",
        "amount",
        "status",
        "requested_by",
        "decided_by",
        "requested_at",
    )
    list_filter = ("status",)
    search_fields = ("payment__admission__student__student_id", "reason")
    list_select_related = ("payment", "requested_by", "decided_by")


@admin.register(FinancialAuditEvent)
class FinancialAuditEventAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "event_type", "admission", "actor", "amount")
    list_filter = ("event_type",)
    search_fields = ("admission__student__student_id", "detail")
    list_select_related = ("admission__student", "actor", "payment")

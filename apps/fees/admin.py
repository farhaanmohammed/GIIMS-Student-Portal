from django.contrib import admin

from apps.fees.models import (
    DueDateChangeRequest,
    FeeInstallment,
    FeePlan,
    InstallmentDueDateRevision,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """
    Fees are financial records: writes must go through ``apps.fees.services``
    so totals are re-derived and audit rows are created. The admin is an
    inspection surface only.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FeePlan)
class FeePlanAdmin(ReadOnlyAdmin):
    list_display = (
        "admission",
        "version",
        "status",
        "course_fee",
        "gst_rate",
        "gst_amount",
        "total_payable",
        "approved_at",
    )
    list_filter = ("status", "currency")
    search_fields = (
        "admission__student__student_id",
        "admission__student__first_name",
        "admission__student__last_name",
    )
    list_select_related = ("admission__student", "admission__centre")
    ordering = ("-created_at",)


@admin.register(FeeInstallment)
class FeeInstallmentAdmin(ReadOnlyAdmin):
    list_display = (
        "fee_plan",
        "sequence",
        "category",
        "description",
        "amount",
        "original_due_date",
    )
    list_filter = ("category",)
    search_fields = ("description", "fee_plan__admission__student__student_id")
    list_select_related = ("fee_plan__admission__student",)


@admin.register(InstallmentDueDateRevision)
class InstallmentDueDateRevisionAdmin(ReadOnlyAdmin):
    list_display = (
        "installment",
        "previous_due_date",
        "revised_due_date",
        "revised_by",
        "created_at",
    )
    search_fields = ("installment__fee_plan__admission__student__student_id", "reason")
    list_select_related = ("installment", "revised_by", "source_request")


@admin.register(DueDateChangeRequest)
class DueDateChangeRequestAdmin(ReadOnlyAdmin):
    list_display = (
        "installment",
        "status",
        "previous_due_date",
        "proposed_due_date",
        "requested_by",
        "decided_by",
        "requested_at",
    )
    list_filter = ("status",)
    search_fields = (
        "installment__fee_plan__admission__student__student_id",
        "reason",
        "decision_note",
    )
    list_select_related = ("installment", "requested_by", "decided_by")

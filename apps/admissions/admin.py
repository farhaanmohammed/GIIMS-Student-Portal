from django.contrib import admin

from apps.admissions.models import Admission


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "centre",
        "course",
        "batch",
        "academic_year",
        "status",
        "enrolled_at",
    )
    list_filter = ("status", "centre", "academic_year", "course")
    search_fields = ("student__student_id", "student__first_name", "student__last_name")
    readonly_fields = ("enquired_at", "created_at", "updated_at", "enrolled_at", "enrolled_by")
    list_select_related = ("student", "centre", "course", "batch", "academic_year")
    ordering = ("-enquired_at",)

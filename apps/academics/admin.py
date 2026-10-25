from django.contrib import admin

from apps.academics.models import AcademicYear, Batch, Course


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("label", "start_date", "end_date", "is_active", "created_at")
    list_filter = ("is_active", "start_date", "end_date")
    search_fields = ("label",)
    ordering = ("-start_date",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "duration_months", "is_active", "created_at")
    list_filter = ("is_active", "duration_months")
    search_fields = ("code", "name", "description")
    ordering = ("code",)


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "course",
        "academic_year",
        "centre",
        "start_date",
        "end_date",
        "capacity",
        "is_active",
    )
    list_filter = ("is_active", "academic_year", "centre", "course")
    search_fields = (
        "code",
        "course__code",
        "course__name",
        "academic_year__label",
        "centre__code",
        "centre__name",
    )
    autocomplete_fields = ("centre", "course", "academic_year")
    list_select_related = ("centre", "course", "academic_year")
    ordering = ("-academic_year__start_date", "course__code", "code")

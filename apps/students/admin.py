from django.contrib import admin

from apps.students.models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "get_full_name", "phone", "guardian_name", "created_at")
    list_filter = ("gender",)
    search_fields = ("student_id", "first_name", "last_name", "phone", "guardian_name")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("last_name", "first_name")

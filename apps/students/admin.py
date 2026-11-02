from django.contrib import admin

from apps.students.models import Student, StudentIdSequence


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "get_full_name", "phone", "guardian_name", "created_at")
    list_filter = ("gender",)
    search_fields = ("student_id", "first_name", "last_name", "phone", "guardian_name")
    readonly_fields = ("student_id", "created_at", "updated_at")
    ordering = ("last_name", "first_name")

    @admin.display(description="Name")
    def get_full_name(self, obj: Student) -> str:
        return obj.get_full_name()


@admin.register(StudentIdSequence)
class StudentIdSequenceAdmin(admin.ModelAdmin):
    """Read-only ops view: IDs are issued only by the enrollment service."""

    list_display = ("prefix", "last_number", "updated_at")
    search_fields = ("prefix",)
    readonly_fields = ("prefix", "last_number", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

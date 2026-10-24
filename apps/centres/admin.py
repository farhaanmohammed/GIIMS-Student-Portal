from django.contrib import admin

from apps.centres.models import Centre


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "email", "phone", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "email", "phone")
    ordering = ("code",)

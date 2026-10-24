from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "role",
        "primary_centre",
        "can_access_all_centres",
        "is_active",
        "is_staff",
    )
    list_filter = ("role", "can_access_all_centres", "is_active", "is_staff")
    search_fields = ("username", "first_name", "last_name", "email", "phone")
    filter_horizontal = ("centres", "groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone")}),
        (
            "Institutional Access & Roles",
            {
                "fields": (
                    "role",
                    "primary_centre",
                    "can_access_all_centres",
                    "centres",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

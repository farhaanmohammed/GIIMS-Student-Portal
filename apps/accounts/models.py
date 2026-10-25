from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model for the GIIMS Student Portal.
    Enforces role-based permissions and centre-level scoping.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        ADMISSIONS = "ADMISSIONS", "Admissions Staff"
        ACCOUNTS = "ACCOUNTS", "Accounts Staff"
        MANAGEMENT = "MANAGEMENT", "Management"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.ADMISSIONS,
        db_index=True,
        help_text="Institutional role determining operational permissions.",
    )
    can_access_all_centres = models.BooleanField(
        default=False,
        help_text="Explicit privilege granting cross-centre visibility.",
    )
    centres = models.ManyToManyField(
        "centres.Centre",
        blank=True,
        related_name="assigned_users",
        help_text="Centres this user is authorized to access.",
    )
    primary_centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_users",
        help_text="Default/primary centre for data entry and views.",
    )
    phone = models.CharField(max_length=25, blank=True, help_text="Official contact phone number.")

    class Meta:
        ordering = ("username",)
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self) -> str:
        full_name = self.get_full_name()
        display = full_name if full_name else self.username
        return f"{display} ({self.get_role_display()})"

    @property
    def has_all_centres_access(self) -> bool:
        """
        Admins, Management, and designated cross-centre operators can access all active centres.
        """
        return bool(
            self.is_superuser
            or self.role in (self.Role.ADMIN, self.Role.MANAGEMENT)
            or self.can_access_all_centres
        )

    def get_accessible_centres(self) -> models.QuerySet:
        """
        Returns the queryset of active centres this user is permitted to view/manage.
        """
        from apps.centres.models import Centre

        if self.has_all_centres_access:
            return Centre.objects.filter(is_active=True)
        return self.centres.filter(is_active=True)

    def can_access_centre(self, centre: Any) -> bool:
        """
        Checks whether the user has access to a specific centre object, pk, or code.

        Non-object identifiers are resolved against active centres so an inactive
        centre can never be authorized by passing only its primary key.
        """
        if not centre:
            return False

        from apps.centres.models import Centre

        if not isinstance(centre, Centre):
            lookup = {"pk": centre} if isinstance(centre, int) else {"code": str(centre)}
            centre = Centre.objects.filter(**lookup).first()
            if centre is None:
                return False

        if not centre.is_active:
            return False
        if self.has_all_centres_access:
            return True
        return self.centres.filter(pk=centre.pk, is_active=True).exists()

    @property
    def is_admin_role(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_admissions_role(self) -> bool:
        return self.role == self.Role.ADMISSIONS

    @property
    def is_accounts_role(self) -> bool:
        return self.role == self.Role.ACCOUNTS

    @property
    def is_management_role(self) -> bool:
        return self.role == self.Role.MANAGEMENT

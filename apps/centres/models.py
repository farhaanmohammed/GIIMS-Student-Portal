from django.db import models

from apps.core.models import TimeStampedModel


class Centre(TimeStampedModel):
    """
    Represents a GIIMS branch / centre campus.
    Serves as the primary operational and security boundary for role-based
    student, admission, and financial scoping.
    """

    code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Unique centre identifier (e.g. KC-01, MC-02).",
    )
    name = models.CharField(max_length=150, help_text="Full centre name (e.g. Kochi Main Campus).")
    address = models.TextField(blank=True, help_text="Physical address of the centre.")
    email = models.EmailField(blank=True, help_text="Official centre contact email.")
    phone = models.CharField(
        max_length=25,
        blank=True,
        help_text="Official centre contact phone number.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Designates whether this centre is active for enrolments and collections.",
    )

    class Meta:
        ordering = ("code",)
        verbose_name = "Centre"
        verbose_name_plural = "Centres"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

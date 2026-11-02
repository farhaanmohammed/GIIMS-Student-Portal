from typing import ClassVar

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Student(TimeStampedModel):
    """
    Durable student identity. The student_id is assigned on enrollment
    and never changes, even if the student changes course later.
    """

    student_id = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        db_index=True,
        help_text="Permanent student ID assigned on enrollment (e.g. TVM-ASP-AD_001).",
    )

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)

    date_of_birth = models.DateField()
    gender = models.CharField(
        max_length=10,
        choices=[
            ("MALE", "Male"),
            ("FEMALE", "Female"),
            ("OTHER", "Other"),
        ],
    )

    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True)

    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)

    guardian_name = models.CharField(max_length=150)
    guardian_phone = models.CharField(max_length=15)
    guardian_relation = models.CharField(max_length=50)

    class Meta:
        ordering = ("last_name", "first_name")
        verbose_name = "Student"
        verbose_name_plural = "Students"

    def __str__(self) -> str:
        if self.student_id:
            return f"{self.student_id} - {self.get_full_name()}"
        return f"{self.get_full_name()} (Not Enrolled)"

    def get_full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip()

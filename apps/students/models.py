from typing import ClassVar

from django.db import models

from apps.core.models import TimeStampedModel


class Student(TimeStampedModel):
    """
    Durable student identity.

    ``student_id`` is assigned once at enrollment (for example
    ``TVM-ASP-AD_001``) and never changes afterwards, even when the student
    later changes course.
    """

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        OTHER = "OTHER", "Other"

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
    gender = models.CharField(max_length=10, choices=Gender.choices)

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


class StudentIdSequence(TimeStampedModel):
    """
    Concurrency-safe counter backing student ID generation.

    One row exists per ID prefix (for example ``TVM-ASP-AD``). The row is
    locked with ``select_for_update()`` while the next number is reserved so
    two concurrent enrollments can never receive the same student ID.

    The prefix embeds the batch code, which changes each academic year, so
    the sequence naturally restarts per year without storing a year column.
    """

    prefix = models.CharField(
        max_length=60,
        unique=True,
        help_text="ID prefix without the numeric suffix (e.g. TVM-ASP-AD).",
    )
    last_number = models.PositiveIntegerField(
        default=0,
        help_text="Highest number already issued for this prefix.",
    )

    class Meta:
        verbose_name = "Student ID Sequence"
        verbose_name_plural = "Student ID Sequences"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(last_number__gte=0),
                name="student_id_sequence_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.prefix}: {self.last_number:03d}"

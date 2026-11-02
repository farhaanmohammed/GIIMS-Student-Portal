from typing import ClassVar

from django.conf import settings
from django.db import models

from apps.core.permissions import CentreScopedModel


class Admission(CentreScopedModel):
    """
    Enrollment event for a student. A student may have multiple admissions
    (e.g. changing course) but keeps the same Student record and student_id.
    No separate admission_id - student_id is the identifier.
    """

    class Status(models.TextChoices):
        ENQUIRED = "ENQUIRED", "Enquired"
        ENROLLED = "ENROLLED", "Enrolled"
        DROPPED = "DROPPED", "Dropped"
        COMPLETED = "COMPLETED", "Completed"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="admissions",
    )
    course = models.ForeignKey(
        "academics.Course",
        on_delete=models.PROTECT,
        related_name="admissions",
    )
    batch = models.ForeignKey(
        "academics.Batch",
        on_delete=models.PROTECT,
        related_name="admissions",
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="admissions",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ENQUIRED,
        db_index=True,
    )

    enquired_at = models.DateTimeField(auto_now_add=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enrollments_done",
    )

    class Meta:
        ordering = ("-enquired_at",)
        verbose_name = "Admission"
        verbose_name_plural = "Admissions"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["student", "batch"],
                name="admission_unique_student_batch",
                condition=models.Q(status__in=["ENQUIRED", "ENROLLED"]),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.batch} ({self.status})"

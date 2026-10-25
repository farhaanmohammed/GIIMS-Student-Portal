from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.permissions import CentreScopedModel

COURSE_CODE_VALIDATOR = RegexValidator(
    regex=r"^[A-Z0-9][A-Z0-9-]*$",
    message="Enter an uppercase alphanumeric course code using hyphens only (e.g. AVM-01).",
)


class AcademicYear(TimeStampedModel):
    """
    A portal-wide academic year (for example "2026-2027").

    The label is free text: no separator or year-format convention is
    enforced because that business decision is not yet confirmed.
    """

    label = models.CharField(
        max_length=20,
        unique=True,
        help_text="Academic year label (e.g. 2026-2027). Free text.",
    )
    start_date = models.DateField(help_text="First day of the academic year.")
    end_date = models.DateField(help_text="Last day of the academic year.")
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Designates whether this academic year is open for new enrolments.",
    )

    class Meta:
        ordering = ("-start_date",)
        verbose_name = "Academic Year"
        verbose_name_plural = "Academic Years"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")),
                name="academic_year_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def is_current(self) -> bool:
        """True when today falls within [start_date, end_date]."""
        today = timezone.localdate()
        return self.start_date <= today <= self.end_date

    def clean(self) -> None:
        super().clean()
        if isinstance(self.label, str):
            self.label = self.label.strip()

    def save(self, *args, **kwargs):
        if isinstance(self.label, str):
            self.label = self.label.strip()
        super().save(*args, **kwargs)


class Course(TimeStampedModel):
    """
    A portal-wide course master record.

    Fee/pricing templates are intentionally not modelled here; they are
    deferred pending confirmed financial decisions.
    """

    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[COURSE_CODE_VALIDATOR],
        help_text="Unique uppercase alphanumeric code with optional hyphens (e.g. AVM-01).",
    )
    name = models.CharField(
        max_length=150, help_text="Full course name (e.g. Aviation Management)."
    )
    duration_months = models.PositiveSmallIntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        help_text="Standard course duration in months (1-120).",
    )
    description = models.TextField(blank=True, help_text="Optional course description.")
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Designates whether this course is active for new batches and admissions.",
    )

    class Meta:
        ordering = ("code",)
        verbose_name = "Course"
        verbose_name_plural = "Courses"

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def _normalize_code(self) -> None:
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()

    def clean_fields(self, exclude=None) -> None:
        # ``full_clean()`` runs field validators before ``clean()``, so the code
        # must be normalized first for lowercase input to be accepted.
        self._normalize_code()
        super().clean_fields(exclude=exclude)

    def clean(self) -> None:
        super().clean()
        self._normalize_code()
        try:
            COURSE_CODE_VALIDATOR(self.code or "")
        except ValidationError as exc:
            raise ValidationError({"code": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self._normalize_code()
        super().save(*args, **kwargs)


class Batch(CentreScopedModel):
    """
    A course intake delivered at a single centre for a given academic year.
    """

    course = models.ForeignKey(
        "academics.Course",
        on_delete=models.PROTECT,
        related_name="batches",
        help_text="Course delivered by this batch.",
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="batches",
        help_text="Academic year this batch belongs to.",
    )
    code = models.CharField(
        max_length=30,
        help_text="Batch identifier within the centre/course/academic year (e.g. 2026-A).",
    )
    start_date = models.DateField(help_text="First day of the batch.")
    end_date = models.DateField(help_text="Last day of the batch.")
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Maximum number of students, when specified. "
            "Leave blank when the centre has not set a capacity."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Designates whether this batch accepts new admissions.",
    )

    class Meta:
        ordering = ("-academic_year__start_date", "course__code", "code")
        verbose_name = "Batch"
        verbose_name_plural = "Batches"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["centre", "course", "academic_year", "code"],
                name="batch_unique_code_per_centre_course_year",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")),
                name="batch_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(capacity__isnull=True) | Q(capacity__gte=1),
                name="batch_capacity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.course.code} {self.academic_year.label} / {self.code} ({self.centre.code})"

    def _normalize_code(self) -> None:
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()

    def clean_fields(self, exclude=None) -> None:
        self._normalize_code()
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        self._normalize_code()
        super().save(*args, **kwargs)

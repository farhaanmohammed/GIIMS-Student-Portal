"""
Plain factory helpers for academics tests.

These intentionally avoid a third-party factory dependency. All data is
synthetic. Dates default relative to ``timezone.localdate()`` so that
``is_current`` assertions stay deterministic without mocking time.
"""

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.academics.models import AcademicYear, Batch, Course
from apps.centres.models import Centre

User = get_user_model()

# Synthetic test credential, not a real secret.
TEST_PASSWORD = "SyntheticTestPass123!"


def make_centre(code: str = "KC-01", name: str = "Kochi Campus", **overrides: Any) -> Centre:
    return Centre.objects.create(code=code, name=name, **overrides)


def make_academic_year(
    label: str = "2026-2027",
    *,
    start_date=None,
    end_date=None,
    **overrides: Any,
) -> AcademicYear:
    today = timezone.localdate()
    start = start_date if start_date is not None else today - timedelta(days=30)
    end = end_date if end_date is not None else today + timedelta(days=335)
    return AcademicYear.objects.create(
        label=label,
        start_date=start,
        end_date=end,
        **overrides,
    )


def make_course(
    code: str = "AVM-01",
    name: str = "Aviation Management",
    **overrides: Any,
) -> Course:
    return Course.objects.create(code=code, name=name, **overrides)


def make_batch(
    centre: Centre,
    course: Course,
    academic_year: AcademicYear,
    code: str = "2026-A",
    *,
    start_date=None,
    end_date=None,
    **overrides: Any,
) -> Batch:
    today = timezone.localdate()
    start = start_date if start_date is not None else today - timedelta(days=10)
    end = end_date if end_date is not None else today + timedelta(days=300)
    return Batch.objects.create(
        centre=centre,
        course=course,
        academic_year=academic_year,
        code=code,
        start_date=start,
        end_date=end,
        **overrides,
    )


def make_user(
    username: str = "academics_user",
    *,
    role: str = User.Role.ADMISSIONS,
    password: str = TEST_PASSWORD,
    **overrides: Any,
):
    return User.objects.create_user(
        username=username,
        password=password,
        role=role,
        **overrides,
    )

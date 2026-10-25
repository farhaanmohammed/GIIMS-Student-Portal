from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.academics.models import AcademicYear, Batch, Course
from apps.academics.tests.factories import (
    make_academic_year,
    make_batch,
    make_centre,
    make_course,
)


class AcademicYearModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_academic_year(label="2026-2027")

    def test_str_returns_label(self):
        self.assertEqual(str(self.year), "2026-2027")

    def test_is_current_true_on_start_date(self):
        today = timezone.localdate()
        year = make_academic_year(
            label="Boundary Start",
            start_date=today,
            end_date=today + timedelta(days=1),
        )
        self.assertTrue(year.is_current)

    def test_is_current_true_on_end_date(self):
        today = timezone.localdate()
        year = make_academic_year(
            label="Boundary End",
            start_date=today - timedelta(days=1),
            end_date=today,
        )
        self.assertTrue(year.is_current)

    def test_is_current_true_when_today_is_inside_range(self):
        self.assertTrue(self.year.is_current)

    def test_is_current_false_the_day_before_start_date(self):
        today = timezone.localdate()
        year = make_academic_year(
            label="Future Year",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=30),
        )
        self.assertFalse(year.is_current)

    def test_is_current_false_the_day_after_end_date(self):
        today = timezone.localdate()
        year = make_academic_year(
            label="Past Year",
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
        )
        self.assertFalse(year.is_current)

    def test_is_active_defaults_to_false(self):
        self.assertFalse(self.year.is_active)

    def test_label_is_stripped_on_save(self):
        year = make_academic_year(label="  2027-2028  ")
        self.assertEqual(year.label, "2027-2028")
        self.assertEqual(AcademicYear.objects.get(pk=year.pk).label, "2027-2028")

    def test_duplicate_label_raises_integrity_error(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_academic_year(label="2026-2027")

    def test_end_date_must_be_after_start_date(self):
        today = timezone.localdate()
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                make_academic_year(
                    label="Reversed Year",
                    start_date=today,
                    end_date=today - timedelta(days=1),
                )

        self.assertIn("academic_year_end_after_start", str(raised.exception))

    def test_equal_start_and_end_dates_are_rejected(self):
        today = timezone.localdate()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_academic_year(
                    label="Single Day Year",
                    start_date=today,
                    end_date=today,
                )

    def test_created_and_updated_timestamps_are_populated(self):
        self.assertIsNotNone(self.year.created_at)
        self.assertIsNotNone(self.year.updated_at)


class CourseModelTests(TestCase):
    def test_str_returns_code_and_name(self):
        course = make_course()
        self.assertEqual(str(course), "AVM-01 - Aviation Management")

    def test_code_is_stripped_and_uppercased_on_save(self):
        course = make_course(code="  avm-01  ")
        self.assertEqual(course.code, "AVM-01")
        self.assertEqual(Course.objects.get(pk=course.pk).code, "AVM-01")

    def test_duplicate_code_raises_integrity_error(self):
        make_course(code="AVM-01")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_course(code="avm-01")

    def test_full_clean_normalizes_and_accepts_valid_code(self):
        """
        ``full_clean()`` normalizes the code before field validators run, so
        lowercase/whitespace input is accepted and stored as uppercase.
        """
        course = Course(code="  avm-01  ", name="Aviation Management")
        course.full_clean()
        self.assertEqual(course.code, "AVM-01")

    def test_full_clean_rejects_invalid_code_shapes(self):
        for invalid_code in ("AVM 01", "AVM_01", "-AVM", ""):
            with self.subTest(code=invalid_code):
                course = Course(code=invalid_code, name="Aviation Management")
                with self.assertRaises(ValidationError) as raised:
                    course.full_clean()
                self.assertIn("code", raised.exception.message_dict)

    def test_duration_months_rejects_values_below_one_and_above_120(self):
        for invalid_duration in (0, 121):
            with self.subTest(duration=invalid_duration):
                course = Course(
                    code="AVM-01",
                    name="Aviation Management",
                    duration_months=invalid_duration,
                )
                with self.assertRaises(ValidationError) as raised:
                    course.full_clean()
                self.assertIn("duration_months", raised.exception.message_dict)

    def test_duration_months_accepts_boundary_values(self):
        for valid_duration in (1, 120):
            with self.subTest(duration=valid_duration):
                course = Course(
                    code="AVM-01",
                    name="Aviation Management",
                    duration_months=valid_duration,
                )
                course.full_clean()

    def test_defaults(self):
        course = make_course()
        self.assertEqual(course.duration_months, 12)
        self.assertIs(course.is_active, True)
        self.assertEqual(course.description, "")


class BatchModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.centre = make_centre()
        cls.other_centre = make_centre(code="AVM-02", name="Aluva Campus")
        cls.course = make_course()
        cls.other_course = make_course(code="BBA-01", name="Business Administration")
        cls.year = make_academic_year()
        cls.other_year = make_academic_year(label="2027-2028")
        cls.batch = make_batch(cls.centre, cls.course, cls.year, code="2026-A")

    def test_str_uses_centre_code_not_name(self):
        self.assertEqual(str(self.batch), "AVM-01 2026-2027 / 2026-A (KC-01)")

    def test_code_is_stripped_and_uppercased_on_save(self):
        batch = make_batch(self.centre, self.course, self.year, code="  2026-b ")
        self.assertEqual(batch.code, "2026-B")
        self.assertEqual(Batch.objects.get(pk=batch.pk).code, "2026-B")

    def test_duplicate_code_in_same_centre_course_and_year_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                make_batch(self.centre, self.course, self.year, code="2026-A")

        self.assertIn("batch_unique_code_per_centre_course_year", str(raised.exception))

    def test_same_code_in_a_different_centre_is_allowed(self):
        batch = make_batch(self.other_centre, self.course, self.year, code="2026-A")
        self.assertTrue(Batch.objects.filter(pk=batch.pk).exists())

    def test_same_code_in_a_different_course_is_allowed(self):
        batch = make_batch(self.centre, self.other_course, self.year, code="2026-A")
        self.assertTrue(Batch.objects.filter(pk=batch.pk).exists())

    def test_same_code_in_a_different_academic_year_is_allowed(self):
        batch = make_batch(self.centre, self.course, self.other_year, code="2026-A")
        self.assertTrue(Batch.objects.filter(pk=batch.pk).exists())

    def test_capacity_defaults_to_none_and_is_allowed(self):
        self.assertIsNone(self.batch.capacity)

        batch = make_batch(
            self.centre,
            self.course,
            self.other_year,
            code="2026-N",
            capacity=None,
        )
        self.assertIsNone(batch.capacity)

    def test_capacity_of_one_is_allowed(self):
        batch = make_batch(self.centre, self.course, self.other_year, code="2026-B", capacity=1)
        self.assertEqual(batch.capacity, 1)

    def test_capacity_of_zero_is_rejected(self):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                make_batch(
                    self.centre,
                    self.course,
                    self.other_year,
                    code="2026-B",
                    capacity=0,
                )

        self.assertIn("batch_capacity_positive", str(raised.exception))

    def test_end_date_must_be_after_start_date(self):
        today = timezone.localdate()
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                make_batch(
                    self.centre,
                    self.course,
                    self.year,
                    code="2026-R",
                    start_date=today,
                    end_date=today - timedelta(days=1),
                )

        self.assertIn("batch_end_after_start", str(raised.exception))

    def test_deleting_a_referenced_centre_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.centre.delete()

    def test_deleting_a_referenced_course_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.course.delete()

    def test_deleting_a_referenced_academic_year_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.year.delete()

    def test_reverse_related_names(self):
        self.assertEqual(list(self.course.batches.all()), [self.batch])
        self.assertEqual(list(self.year.batches.all()), [self.batch])
        self.assertEqual(list(self.centre.academics_batch_set.all()), [self.batch])

    def test_created_and_updated_timestamps_are_populated(self):
        self.assertIsNotNone(self.batch.created_at)
        self.assertIsNotNone(self.batch.updated_at)

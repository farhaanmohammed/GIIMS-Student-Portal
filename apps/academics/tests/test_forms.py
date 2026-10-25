"""Form-level validation tests for the academics master-data forms."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.academics.forms import AcademicYearForm, BatchForm, CourseForm
from apps.academics.models import AcademicYear, Batch, Course
from apps.academics.tests.factories import (
    make_academic_year,
    make_batch,
    make_centre,
    make_course,
    make_user,
)

User = get_user_model()


def _date(days_from_today: int) -> str:
    """Return an HTML date input value relative to the current local date."""
    return (timezone.localdate() + timedelta(days=days_from_today)).isoformat()


class AcademicYearFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.existing = make_academic_year(label="2025-2026")

    def _data(self, **overrides):
        data = {
            "label": "2026-2027",
            "start_date": _date(0),
            "end_date": _date(365),
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_valid_form_creates_academic_year(self):
        form = AcademicYearForm(data=self._data())

        self.assertTrue(form.is_valid(), form.errors)
        year = form.save()

        self.assertEqual(AcademicYear.objects.count(), 2)
        self.assertEqual(year.label, "2026-2027")
        self.assertTrue(year.is_active)

    def test_label_is_stripped_before_it_is_stored(self):
        form = AcademicYearForm(data=self._data(label="  2026-2027  "))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["label"], "2026-2027")

        year = form.save()
        self.assertEqual(year.label, "2026-2027")
        self.assertEqual(AcademicYear.objects.get(pk=year.pk).label, "2026-2027")

    def test_end_date_equal_to_start_date_is_rejected(self):
        form = AcademicYearForm(data=self._data(start_date=_date(0), end_date=_date(0)))

        self.assertFalse(form.is_valid())
        self.assertIn("end_date", form.errors)
        self.assertIn("End date must be after the start date.", form.errors["end_date"])

    def test_end_date_before_start_date_is_rejected(self):
        form = AcademicYearForm(data=self._data(start_date=_date(10), end_date=_date(9)))

        self.assertFalse(form.is_valid())
        self.assertIn("end_date", form.errors)
        self.assertIn("End date must be after the start date.", form.errors["end_date"])

    def test_missing_required_fields_are_rejected(self):
        form = AcademicYearForm(data={})

        self.assertFalse(form.is_valid())
        for field_name in ("label", "start_date", "end_date"):
            with self.subTest(field=field_name):
                self.assertIn(field_name, form.errors)

    def test_duplicate_label_is_rejected(self):
        form = AcademicYearForm(
            data=self._data(label="  2025-2026  ", start_date=_date(0), end_date=_date(365))
        )

        self.assertFalse(form.is_valid())
        self.assertIn("label", form.errors)
        self.assertIn("already exists", str(form.errors["label"]).lower())
        self.assertEqual(AcademicYear.objects.count(), 1)


class CourseFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.existing = make_course(code="AVM-01", name="Aviation Management")

    def _data(self, **overrides):
        data = {
            "code": "BBA-01",
            "name": "Business Administration",
            "duration_months": 24,
            "description": "",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_valid_form_creates_course(self):
        form = CourseForm(data=self._data())

        self.assertTrue(form.is_valid(), form.errors)
        course = form.save()

        self.assertEqual(Course.objects.count(), 2)
        self.assertEqual(course.code, "BBA-01")
        self.assertEqual(course.duration_months, 24)
        self.assertTrue(course.is_active)

    def test_lowercase_and_padded_code_is_normalized(self):
        form = CourseForm(data=self._data(code="  bba-01  "))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "BBA-01")

        course = form.save()
        self.assertEqual(course.code, "BBA-01")
        self.assertEqual(Course.objects.get(pk=course.pk).code, "BBA-01")

    def test_invalid_code_shapes_are_rejected(self):
        for invalid_code in ("AVM 01", "AVM_01", "-AVM", ""):
            with self.subTest(code=invalid_code):
                form = CourseForm(data=self._data(code=invalid_code))

                self.assertFalse(form.is_valid())
                self.assertIn("code", form.errors)
                self.assertEqual(Course.objects.count(), 1)

    def test_duplicate_code_is_rejected_after_normalization(self):
        form = CourseForm(data=self._data(code="  avm-01  "))

        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)
        self.assertIn("already exists", str(form.errors["code"]).lower())
        self.assertEqual(Course.objects.count(), 1)

    def test_duration_months_boundaries_are_enforced(self):
        for invalid_duration in (0, 121):
            with self.subTest(duration=invalid_duration):
                form = CourseForm(data=self._data(duration_months=invalid_duration))

                self.assertFalse(form.is_valid())
                self.assertIn("duration_months", form.errors)

        for valid_duration in (1, 120):
            with self.subTest(duration=valid_duration):
                form = CourseForm(
                    data=self._data(code=f"DUR-{valid_duration}", duration_months=valid_duration)
                )

                self.assertTrue(form.is_valid(), form.errors)
                form.save()

    def test_editing_a_course_does_not_trigger_its_own_unique_code_error(self):
        form = CourseForm(
            instance=self.existing,
            data=self._data(code="avm-01", name="Aviation Management (revised)"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        course = form.save()

        self.assertEqual(course.pk, self.existing.pk)
        self.assertEqual(course.code, "AVM-01")
        self.assertEqual(course.name, "Aviation Management (revised)")


class BatchFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kochi = make_centre()
        cls.aluva = make_centre(code="AVM-02", name="Aluva Campus")
        cls.course = make_course()
        cls.year = make_academic_year()
        cls.batch = make_batch(cls.kochi, cls.course, cls.year, code="2026-A")
        cls.scoped_user = make_user(username="forms_scoped_clerk", role=User.Role.ADMISSIONS)
        cls.scoped_user.centres.add(cls.kochi)
        cls.manager = make_user(username="forms_manager", role=User.Role.MANAGEMENT)

    def _data(self, **overrides):
        data = {
            "centre": self.kochi.pk,
            "course": self.course.pk,
            "academic_year": self.year.pk,
            "code": "2026-B",
            "start_date": _date(0),
            "end_date": _date(300),
            "capacity": "",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_form_without_a_user_offers_no_centre_choices_and_rejects_submission(self):
        form = BatchForm()
        self.assertEqual(list(form.fields["centre"].queryset), [])

        bound_form = BatchForm(data=self._data())
        self.assertFalse(bound_form.is_valid())
        self.assertIn("centre", bound_form.errors)
        self.assertEqual(Batch.objects.count(), 1)

    def test_scoped_user_centre_choices_exclude_inaccessible_centres(self):
        form = BatchForm(user=self.scoped_user)

        self.assertEqual(list(form.fields["centre"].queryset), [self.kochi])

    def test_inaccessible_centre_submission_is_rejected(self):
        form = BatchForm(data=self._data(centre=self.aluva.pk), user=self.scoped_user)

        self.assertFalse(form.is_valid())
        self.assertIn("centre", form.errors)
        self.assertEqual(Batch.objects.count(), 1)

    def test_code_is_normalized_and_stored_uppercase(self):
        form = BatchForm(data=self._data(code="  2026-b "), user=self.manager)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "2026-B")

        batch = form.save()
        self.assertEqual(batch.code, "2026-B")

    def test_code_is_required(self):
        form = BatchForm(data=self._data(code="   "), user=self.manager)

        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_reversed_dates_are_rejected(self):
        form = BatchForm(
            data=self._data(start_date=_date(10), end_date=_date(9)), user=self.manager
        )

        self.assertFalse(form.is_valid())
        self.assertIn("end_date", form.errors)
        self.assertIn("End date must be after the start date.", form.errors["end_date"])

    def test_blank_capacity_is_stored_as_none(self):
        form = BatchForm(data=self._data(capacity=""), user=self.manager)

        self.assertTrue(form.is_valid(), form.errors)
        batch = form.save()

        self.assertIsNone(batch.capacity)
        self.assertIsNone(Batch.objects.get(pk=batch.pk).capacity)

    def test_zero_and_negative_capacity_are_rejected(self):
        for invalid_capacity in ("0", "-5"):
            with self.subTest(capacity=invalid_capacity):
                form = BatchForm(
                    data=self._data(code=f"CAP-{invalid_capacity}", capacity=invalid_capacity),
                    user=self.manager,
                )

                self.assertFalse(form.is_valid())
                self.assertIn("capacity", form.errors)

    def test_capacity_of_one_is_accepted(self):
        form = BatchForm(data=self._data(code="2026-C", capacity="1"), user=self.manager)

        self.assertTrue(form.is_valid(), form.errors)
        batch = form.save()

        self.assertEqual(batch.capacity, 1)

    def test_editing_a_batch_does_not_trigger_its_own_duplicate_error(self):
        form = BatchForm(
            instance=self.batch,
            data=self._data(code="  2026-a  ", capacity="40"),
            user=self.manager,
        )

        self.assertTrue(form.is_valid(), form.errors)
        batch = form.save()

        self.assertEqual(batch.pk, self.batch.pk)
        self.assertEqual(batch.code, "2026-A")
        self.assertEqual(batch.capacity, 40)

    def test_duplicate_centre_course_year_and_code_is_rejected(self):
        form = BatchForm(data=self._data(code="2026-a"), user=self.manager)

        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)
        self.assertIn("already exists", str(form.errors["code"]).lower())
        self.assertEqual(Batch.objects.count(), 1)

    def test_single_accessible_centre_is_preselected(self):
        form = BatchForm(user=self.scoped_user)

        self.assertEqual(form.initial.get("centre"), self.kochi.pk)


class FormWidgetStylingTests(TestCase):
    """Light-touch checks that inputs carry the shared Tailwind input classes."""

    @classmethod
    def setUpTestData(cls):
        cls.centre = make_centre()
        cls.course = make_course()
        cls.year = make_academic_year()
        cls.manager = make_user(username="forms_styling_manager", role=User.Role.MANAGEMENT)

    def test_text_inputs_share_the_expected_input_classes(self):
        cases = (
            (AcademicYearForm(), "label"),
            (CourseForm(), "code"),
            (BatchForm(user=self.manager), "code"),
        )

        for form, field_name in cases:
            with self.subTest(form=type(form).__name__, field=field_name):
                rendered = str(form[field_name])
                self.assertIn("rounded-lg", rendered)
                self.assertIn("border-slate-300", rendered)

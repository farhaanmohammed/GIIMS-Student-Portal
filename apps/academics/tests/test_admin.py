from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.academics.admin import AcademicYearAdmin, BatchAdmin, CourseAdmin
from apps.academics.models import AcademicYear, Batch, Course
from apps.academics.tests.factories import (
    make_academic_year,
    make_batch,
    make_centre,
    make_course,
    make_user,
)

User = get_user_model()


class AcademicsAdminRegistrationTests(TestCase):
    def test_models_are_registered_with_expected_admin_classes(self):
        expected = (
            (AcademicYear, AcademicYearAdmin),
            (Course, CourseAdmin),
            (Batch, BatchAdmin),
        )
        for model, admin_class in expected:
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))
                self.assertIsInstance(admin.site._registry[model], admin_class)


class AcademicsAdminConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = make_user(
            username="admin_smoke_superuser",
            role=User.Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        cls.centre = make_centre()
        cls.year = make_academic_year()
        cls.course = make_course()
        cls.batch = make_batch(cls.centre, cls.course, cls.year, code="2026-A")

    def setUp(self):
        self.request = RequestFactory().get("/admin/academics/")
        self.request.user = self.superuser

    def _registered_admin(self, model):
        return admin.site._registry[model]

    def _assert_field_path_exists(self, model, path):
        current_model = model
        parts = path.split("__")
        for index, part in enumerate(parts):
            try:
                field = current_model._meta.get_field(part)
            except Exception as exc:  # pragma: no cover - defensive failure message
                self.fail(f"{model.__name__}.{path}: unknown field {part!r} ({exc})")
            if index < len(parts) - 1:
                self.assertIsNotNone(
                    field.related_model,
                    f"{model.__name__}.{path}: {part!r} is not a relation",
                )
                current_model = field.related_model

    def test_batch_admin_selects_related_centre_course_and_year(self):
        model_admin = self._registered_admin(Batch)
        self.assertIsInstance(model_admin, BatchAdmin)
        for field_name in ("centre", "course", "academic_year"):
            with self.subTest(field=field_name):
                self.assertIn(field_name, model_admin.list_select_related)

    def test_list_display_fields_resolve_for_all_admins(self):
        for model in (AcademicYear, Course, Batch):
            model_admin = self._registered_admin(model)
            with self.subTest(model=model.__name__):
                for field_name in model_admin.get_list_display(self.request):
                    if hasattr(model_admin, field_name):
                        continue
                    self._assert_field_path_exists(model, field_name)

    def test_search_fields_resolve_for_all_admins(self):
        for model in (AcademicYear, Course, Batch):
            model_admin = self._registered_admin(model)
            with self.subTest(model=model.__name__):
                for field_name in model_admin.get_search_fields(self.request):
                    self._assert_field_path_exists(model, field_name)

    def test_list_filter_fields_resolve_for_all_admins(self):
        for model in (AcademicYear, Course, Batch):
            model_admin = self._registered_admin(model)
            with self.subTest(model=model.__name__):
                for field_name in model_admin.get_list_filter(self.request):
                    if isinstance(field_name, (tuple, list)):
                        field_name = field_name[0]
                    self._assert_field_path_exists(model, field_name)

    def test_get_queryset_evaluates_without_field_errors(self):
        for model in (AcademicYear, Course, Batch):
            model_admin = self._registered_admin(model)
            with self.subTest(model=model.__name__):
                results = list(model_admin.get_queryset(self.request))
                self.assertTrue(results)

    def test_batch_queryset_is_select_related_and_ordered(self):
        model_admin = self._registered_admin(Batch)
        # ModelAdmin.get_queryset() does not apply list_select_related itself;
        # the changelist does. Apply the configured paths and evaluate them so
        # a typo or non-relation in list_select_related would fail here.
        queryset = model_admin.get_queryset(self.request).select_related(
            *model_admin.get_list_select_related(self.request)
        )
        self.assertIn("centre", queryset.query.select_related)
        self.assertIn("course", queryset.query.select_related)
        self.assertIn("academic_year", queryset.query.select_related)
        self.assertEqual(list(queryset), [self.batch])

    def test_admin_search_finds_records_by_related_fields(self):
        cases = (
            (AcademicYear, "2026-2027"),
            (Course, "Aviation"),
            (Batch, "AVM-01"),
            (Batch, "KC-01"),
            (Batch, "2026-2027"),
        )
        for model, term in cases:
            model_admin = self._registered_admin(model)
            with self.subTest(model=model.__name__, term=term):
                queryset = model_admin.get_queryset(self.request)
                results, _ = model_admin.get_search_results(self.request, queryset, term)
                self.assertTrue(results.exists())

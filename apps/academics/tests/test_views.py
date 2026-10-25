"""View, URL, permission, HTMX, and template tests for academics master data."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class AcademicsAuthorizationMatrixTests(TestCase):
    """Authentication and role gates for every academics endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.centre = make_centre()
        cls.year = make_academic_year()
        cls.course = make_course()
        cls.batch = make_batch(cls.centre, cls.course, cls.year)
        cls.users = {
            role: make_user(username=f"matrix_{role.lower()}", role=role)
            for role in (
                User.Role.ADMIN,
                User.Role.MANAGEMENT,
                User.Role.ADMISSIONS,
                User.Role.ACCOUNTS,
            )
        }

    def _urls(self) -> dict[str, list[str]]:
        return {
            "list": [
                reverse("academics:academic_year_list"),
                reverse("academics:course_list"),
                reverse("academics:batch_list"),
            ],
            "create": [
                reverse("academics:academic_year_create"),
                reverse("academics:course_create"),
                reverse("academics:batch_create"),
            ],
            "update": [
                reverse("academics:academic_year_update", args=[self.year.pk]),
                reverse("academics:course_update", args=[self.course.pk]),
                reverse("academics:batch_update", args=[self.batch.pk]),
            ],
            "toggle": [
                reverse("academics:academic_year_toggle", args=[self.year.pk]),
                reverse("academics:course_toggle", args=[self.course.pk]),
                reverse("academics:batch_toggle", args=[self.batch.pk]),
            ],
        }

    def test_anonymous_users_are_redirected_to_login_with_next(self):
        for kind, urls in self._urls().items():
            for url in urls:
                with self.subTest(kind=kind, url=url):
                    response = self.client.get(url)

                    self.assertEqual(response.status_code, 302)
                    parsed = urlparse(response.url)
                    self.assertEqual(parsed.path, reverse("login"))
                    self.assertEqual(parse_qs(parsed.query)["next"], [url])

    def test_anonymous_posts_are_redirected_to_login_without_writing(self):
        counts_before = (
            AcademicYear.objects.count(),
            Course.objects.count(),
            Batch.objects.count(),
        )
        urls = (
            reverse("academics:academic_year_create"),
            reverse("academics:course_create"),
            reverse("academics:batch_create"),
            reverse("academics:academic_year_toggle", args=[self.year.pk]),
            reverse("academics:course_toggle", args=[self.course.pk]),
            reverse("academics:batch_toggle", args=[self.batch.pk]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.post(url, {})

                self.assertEqual(response.status_code, 302)
                parsed = urlparse(response.url)
                self.assertEqual(parsed.path, reverse("login"))
                self.assertEqual(parse_qs(parsed.query)["next"], [url])

        self.assertEqual(
            (
                AcademicYear.objects.count(),
                Course.objects.count(),
                Batch.objects.count(),
            ),
            counts_before,
        )

    def test_every_authenticated_role_can_read_every_list(self):
        for role, user in self.users.items():
            self.client.force_login(user)
            for url in self._urls()["list"]:
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_read_only_roles_are_forbidden_from_write_endpoints(self):
        for role in (User.Role.ADMISSIONS, User.Role.ACCOUNTS):
            self.client.force_login(self.users[role])
            for kind in ("create", "update", "toggle"):
                for url in self._urls()[kind]:
                    with self.subTest(role=role, kind=kind, url=url):
                        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_and_management_can_open_create_and_update_forms(self):
        for role in (User.Role.ADMIN, User.Role.MANAGEMENT):
            self.client.force_login(self.users[role])
            for kind in ("create", "update"):
                for url in self._urls()[kind]:
                    with self.subTest(role=role, kind=kind, url=url):
                        self.assertEqual(self.client.get(url).status_code, 200)

    def test_admin_and_management_get_method_not_allowed_for_toggles(self):
        for role in (User.Role.ADMIN, User.Role.MANAGEMENT):
            self.client.force_login(self.users[role])
            for url in self._urls()["toggle"]:
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 405)


class AcademicYearCrudViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(username="crud_year_admin", role=User.Role.ADMIN)
        cls.year = make_academic_year(label="2025-2026")

    def setUp(self):
        self.client.force_login(self.admin)

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_create_view_creates_academic_year_and_redirects(self):
        response = self.client.post(
            reverse("academics:academic_year_create"),
            {
                "label": "2026-2027",
                "start_date": _date(0),
                "end_date": _date(365),
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("academics:academic_year_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(AcademicYear.objects.filter(label="2026-2027", is_active=True).exists())
        self.assertEqual(self._messages(response), ['Academic year "2026-2027" created.'])

    def test_update_view_updates_academic_year_and_redirects(self):
        response = self.client.post(
            reverse("academics:academic_year_update", args=[self.year.pk]),
            {
                "label": "2025-2026 revised",
                "start_date": _date(0),
                "end_date": _date(200),
                "is_active": "on",
            },
        )

        self.year.refresh_from_db()
        self.assertEqual(self.year.label, "2025-2026 revised")
        self.assertTrue(self.year.is_active)
        self.assertRedirects(
            response,
            reverse("academics:academic_year_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self._messages(response), ['Academic year "2025-2026 revised" updated.'])

    def test_toggle_view_flips_status_each_time_it_is_posted(self):
        toggle_url = reverse("academics:academic_year_toggle", args=[self.year.pk])
        self.assertFalse(self.year.is_active)

        response = self.client.post(toggle_url, follow=True)
        self.year.refresh_from_db()
        self.assertTrue(self.year.is_active)
        self.assertEqual(
            [str(message) for message in response.context["messages"]],
            ['Academic year "2025-2026" activated.'],
        )

        response = self.client.post(toggle_url, follow=True)
        self.year.refresh_from_db()
        self.assertFalse(self.year.is_active)
        self.assertEqual(
            [str(message) for message in response.context["messages"]],
            ['Academic year "2025-2026" deactivated.'],
        )

    def test_toggle_rejects_get_and_does_not_change_state(self):
        response = self.client.get(reverse("academics:academic_year_toggle", args=[self.year.pk]))

        self.assertEqual(response.status_code, 405)
        self.year.refresh_from_db()
        self.assertFalse(self.year.is_active)

    def test_invalid_create_rerenders_the_form_without_writing(self):
        response = self.client.post(
            reverse("academics:academic_year_create"),
            {
                "label": "2027-2028",
                "start_date": _date(0),
                "end_date": _date(0),
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "academics/academic_year_form.html")
        self.assertIn("end_date", response.context["form"].errors)
        self.assertIn(
            "End date must be after the start date.",
            response.context["form"].errors["end_date"],
        )
        self.assertEqual(AcademicYear.objects.count(), 1)


class CourseCrudViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(username="crud_course_admin", role=User.Role.ADMIN)
        cls.course = make_course(code="AVM-01", name="Aviation Management")

    def setUp(self):
        self.client.force_login(self.admin)

    def _valid_payload(self, **overrides):
        payload = {
            "code": "BBA-01",
            "name": "Business Administration",
            "duration_months": 24,
            "description": "",
            "is_active": "on",
        }
        payload.update(overrides)
        return payload

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_create_view_creates_course_and_redirects(self):
        response = self.client.post(
            reverse("academics:course_create"),
            self._valid_payload(code="  bba-01  "),
        )

        self.assertRedirects(
            response,
            reverse("academics:course_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(Course.objects.filter(code="BBA-01").exists())
        self.assertEqual(self._messages(response), ['Course "BBA-01" created.'])

    def test_update_view_updates_course_and_redirects(self):
        response = self.client.post(
            reverse("academics:course_update", args=[self.course.pk]),
            self._valid_payload(code="AVM-01", name="Aviation Management (revised)"),
        )

        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Aviation Management (revised)")
        self.assertRedirects(
            response,
            reverse("academics:course_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self._messages(response), ['Course "AVM-01" updated.'])

    def test_toggle_view_deactivates_course_and_adds_a_message(self):
        response = self.client.post(reverse("academics:course_toggle", args=[self.course.pk]))

        self.assertRedirects(
            response,
            reverse("academics:course_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self._messages(response), ['Course "AVM-01" deactivated.'])

        self.course.refresh_from_db()
        self.assertFalse(self.course.is_active)

    def test_toggle_rejects_get_and_does_not_change_state(self):
        response = self.client.get(reverse("academics:course_toggle", args=[self.course.pk]))

        self.assertEqual(response.status_code, 405)
        self.course.refresh_from_db()
        self.assertTrue(self.course.is_active)

    def test_invalid_create_rerenders_the_form_without_writing(self):
        response = self.client.post(
            reverse("academics:course_create"),
            self._valid_payload(code="BBA_01"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "academics/course_form.html")
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(Course.objects.count(), 1)


class BatchCrudViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(username="crud_batch_admin", role=User.Role.ADMIN)
        cls.centre = make_centre()
        cls.course = make_course()
        cls.year = make_academic_year()
        cls.batch = make_batch(cls.centre, cls.course, cls.year, code="2026-A")

    def setUp(self):
        self.client.force_login(self.admin)

    def _valid_payload(self, **overrides):
        payload = {
            "centre": self.centre.pk,
            "course": self.course.pk,
            "academic_year": self.year.pk,
            "code": "2026-B",
            "start_date": _date(0),
            "end_date": _date(300),
            "capacity": "",
            "is_active": "on",
        }
        payload.update(overrides)
        return payload

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_create_view_creates_batch_and_redirects(self):
        response = self.client.post(
            reverse("academics:batch_create"),
            self._valid_payload(code="  2026-b "),
        )

        self.assertRedirects(
            response,
            reverse("academics:batch_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(Batch.objects.filter(code="2026-B").exists())
        self.assertEqual(self._messages(response), ['Batch "2026-B" created for AVM-01.'])

    def test_update_view_updates_batch_and_redirects(self):
        response = self.client.post(
            reverse("academics:batch_update", args=[self.batch.pk]),
            self._valid_payload(code="2026-A", capacity="30"),
        )

        self.batch.refresh_from_db()
        self.assertEqual(self.batch.capacity, 30)
        self.assertRedirects(
            response,
            reverse("academics:batch_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self._messages(response), ['Batch "2026-A" updated for AVM-01.'])

    def test_toggle_view_deactivates_batch_and_redirects(self):
        response = self.client.post(
            reverse("academics:batch_toggle", args=[self.batch.pk]),
        )

        self.batch.refresh_from_db()
        self.assertFalse(self.batch.is_active)
        self.assertRedirects(
            response,
            reverse("academics:batch_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self._messages(response), ['Batch "2026-A" deactivated.'])

    def test_toggle_rejects_get_and_does_not_change_state(self):
        response = self.client.get(reverse("academics:batch_toggle", args=[self.batch.pk]))

        self.assertEqual(response.status_code, 405)
        self.batch.refresh_from_db()
        self.assertTrue(self.batch.is_active)

    def test_invalid_create_rerenders_the_form_without_writing(self):
        response = self.client.post(
            reverse("academics:batch_create"),
            self._valid_payload(code="2026-B", start_date=_date(0), end_date=_date(0)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "academics/batch_form.html")
        self.assertIn("end_date", response.context["form"].errors)
        self.assertEqual(Batch.objects.count(), 1)


class BatchCentreScopingViewTests(TestCase):
    """
    Centre scoping guarantees for batch reads and writes.

    A cross-centre ``batch_update`` resolves to 404 through
    ``Batch.objects.for_user``, but that branch is unreachable for
    ADMIN/MANAGEMENT users because ``has_all_centres_access`` is True for those
    roles by design. The write boundary is therefore covered by the list
    scoping tests here and the centre-queryset/IDOR tests in ``test_forms``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.kochi = make_centre()
        cls.aluva = make_centre(code="AVM-02", name="Aluva Campus")
        cls.course = make_course()
        cls.year = make_academic_year()
        cls.kochi_batch = make_batch(cls.kochi, cls.course, cls.year, code="KOCHI-A")
        cls.aluva_batch = make_batch(cls.aluva, cls.course, cls.year, code="ALUVA-A")
        cls.kochi_clerk = make_user(
            username="scoping_kochi_clerk",
            role=User.Role.ADMISSIONS,
        )
        cls.kochi_clerk.centres.add(cls.kochi)
        cls.unassigned_clerk = make_user(
            username="scoping_unassigned_clerk",
            role=User.Role.ADMISSIONS,
        )
        cls.admin = make_user(username="scoping_admin", role=User.Role.ADMIN)

    def test_scoped_user_sees_only_assigned_centre_batches(self):
        self.client.force_login(self.kochi_clerk)

        response = self.client.get(reverse("academics:batch_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["batches"]), [self.kochi_batch])
        self.assertContains(response, "KOCHI-A")
        self.assertNotContains(response, "ALUVA-A")
        self.assertContains(response, "Kochi Campus")
        self.assertNotContains(response, "Aluva Campus")

    def test_user_without_centres_sees_no_batches(self):
        self.client.force_login(self.unassigned_clerk)

        response = self.client.get(reverse("academics:batch_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["batches"]), [])
        self.assertContains(response, "No batches yet")
        self.assertNotContains(response, "KOCHI-A")
        self.assertNotContains(response, "ALUVA-A")

    def test_admin_sees_batches_from_every_centre(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("academics:batch_list"))

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            list(response.context["batches"]),
            [self.kochi_batch, self.aluva_batch],
        )
        self.assertContains(response, "Kochi Campus")
        self.assertContains(response, "Aluva Campus")

    def test_inaccessible_centre_filter_is_ignored_for_scoped_users(self):
        self.client.force_login(self.kochi_clerk)

        response = self.client.get(
            reverse("academics:batch_list"),
            {"centre": self.aluva.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filters"]["centre"], "")
        self.assertEqual(list(response.context["batches"]), [self.kochi_batch])
        self.assertNotContains(response, "ALUVA-A")


class AcademicsHtmxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(username="htmx_admin", role=User.Role.ADMIN)
        cls.kochi = make_centre()
        cls.aluva = make_centre(code="AVM-02", name="Aluva Campus")
        cls.course = make_course(code="AVM-01", name="Aviation Management")
        cls.inactive_course = make_course(
            code="BBA-01",
            name="Business Administration",
            is_active=False,
        )
        cls.year = make_academic_year()
        cls.batch = make_batch(cls.kochi, cls.course, cls.year, code="2026-A")

    def setUp(self):
        self.client.force_login(self.admin)

    def _list_cases(self):
        return (
            (
                reverse("academics:academic_year_list"),
                "academics/academic_year_list.html",
                "academics/partials/_academic_year_table.html",
            ),
            (
                reverse("academics:course_list"),
                "academics/course_list.html",
                "academics/partials/_course_table.html",
            ),
            (
                reverse("academics:batch_list"),
                "academics/batch_list.html",
                "academics/partials/_batch_table.html",
            ),
        )

    def test_htmx_requests_render_only_the_table_partial(self):
        for url, full_template, partial_template in self._list_cases():
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_HX_REQUEST="true")

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, partial_template)
                self.assertTemplateNotUsed(response, full_template)
                self.assertTemplateNotUsed(response, "base.html")
                self.assertNotContains(response, "GIIMS Portal")

    def test_non_htmx_requests_render_the_full_page(self):
        for url, full_template, _partial_template in self._list_cases():
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, full_template)
                self.assertTemplateUsed(response, "base.html")
                self.assertContains(response, "<!DOCTYPE html>")
                self.assertContains(response, "GIIMS Portal")

    def test_malformed_filters_are_ignored(self):
        cases = (
            (
                reverse("academics:academic_year_list"),
                {"is_active": "banana"},
                {"is_active": ""},
            ),
            (
                reverse("academics:course_list"),
                {"is_active": "banana"},
                {"q": "", "is_active": ""},
            ),
            (
                reverse("academics:batch_list"),
                {"centre": "abc", "academic_year": "999999", "is_active": "banana"},
                {"centre": "", "academic_year": "", "is_active": ""},
            ),
            (
                reverse("academics:batch_list"),
                {
                    "centre": "999999999999999999999",
                    "academic_year": "999999999999999999999",
                    "is_active": "banana",
                },
                {"centre": "", "academic_year": "", "is_active": ""},
            ),
        )

        for url, params, expected_filters in cases:
            with self.subTest(url=url):
                response = self.client.get(url, params)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["filters"], expected_filters)

        batch_response = self.client.get(
            reverse("academics:batch_list"),
            {"centre": "abc", "academic_year": "999999", "is_active": "banana"},
        )
        self.assertEqual(list(batch_response.context["batches"]), [self.batch])

    def test_course_status_and_search_filters_apply(self):
        response = self.client.get(
            reverse("academics:course_list"),
            {"is_active": "0"},
        )
        self.assertEqual(list(response.context["courses"]), [self.inactive_course])
        self.assertContains(response, "BBA-01")
        self.assertNotContains(response, "AVM-01")

        response = self.client.get(
            reverse("academics:course_list"),
            {"q": "business"},
        )
        self.assertEqual(list(response.context["courses"]), [self.inactive_course])

    def test_batch_centre_and_academic_year_filters_apply(self):
        other_year = make_academic_year(label="2027-2028")
        aluva_batch = make_batch(self.aluva, self.course, other_year, code="ALUVA-A")

        response = self.client.get(
            reverse("academics:batch_list"),
            {"centre": self.kochi.pk},
        )
        self.assertEqual(list(response.context["batches"]), [self.batch])

        response = self.client.get(
            reverse("academics:batch_list"),
            {"academic_year": other_year.pk},
        )
        self.assertEqual(list(response.context["batches"]), [aluva_batch])

    def test_active_filter_hides_inactive_courses_and_batches(self):
        inactive_batch = make_batch(
            self.kochi,
            self.course,
            self.year,
            code="2026-B",
            is_active=False,
        )

        course_response = self.client.get(
            reverse("academics:course_list"),
            {"is_active": "1"},
        )
        self.assertEqual(list(course_response.context["courses"]), [self.course])
        self.assertNotContains(course_response, "BBA-01")

        batch_response = self.client.get(
            reverse("academics:batch_list"),
            {"is_active": "1"},
        )
        self.assertEqual(list(batch_response.context["batches"]), [self.batch])
        self.assertNotContains(batch_response, inactive_batch.code)


class AcademicsPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user(username="pagination_user", role=User.Role.ADMISSIONS)
        for index in range(1, 27):
            make_course(code=f"CRS-{index:02d}", name=f"Synthetic Course {index}")

    def setUp(self):
        self.client.force_login(self.user)

    def test_second_page_returns_the_remaining_course(self):
        response = self.client.get(reverse("academics:course_list"), {"page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRS-26")
        self.assertNotContains(response, "CRS-01")
        self.assertEqual(response.context["page_obj"].number, 2)

    def test_pagination_links_preserve_the_active_filter(self):
        response = self.client.get(
            reverse("academics:course_list"),
            {"q": "CRS", "page": "2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRS-26")
        self.assertContains(response, "q=CRS&amp;page=1")

    def test_malformed_and_out_of_range_pages_are_tolerated(self):
        for page in ("abc", "999"):
            with self.subTest(page=page):
                response = self.client.get(
                    reverse("academics:course_list"),
                    {"q": "CRS", "page": page},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["total_count"], 26)


class AcademicsTemplateSmokeTests(TestCase):
    """Rendered-page checks for CSRF, labels, and empty states."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(username="template_smoke_admin", role=User.Role.ADMIN)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_create_forms_include_csrf_token_and_labelled_controls(self):
        urls = (
            reverse("academics:academic_year_create"),
            reverse("academics:course_create"),
            reverse("academics:batch_create"),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "base.html")
                self.assertContains(response, "csrfmiddlewaretoken")

                for field in response.context["form"]:
                    with self.subTest(field=field.name):
                        self.assertContains(response, f'for="{field.id_for_label}"')

    def test_empty_lists_render_an_empty_state_message(self):
        cases = (
            (reverse("academics:academic_year_list"), "No academic years yet"),
            (reverse("academics:course_list"), "No courses yet"),
            (reverse("academics:batch_list"), "No batches yet"),
        )

        for url, expected_text in cases:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_text)

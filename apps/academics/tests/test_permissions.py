from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.academics.models import Batch
from apps.academics.tests.factories import (
    make_academic_year,
    make_batch,
    make_centre,
    make_course,
    make_user,
)
from apps.core.permissions import role_required

User = get_user_model()


@role_required(User.Role.ADMIN, User.Role.MANAGEMENT)
def stub_admin_view(request):
    return HttpResponse("Admin Authorized")


@role_required(User.Role.ADMISSIONS)
def stub_admissions_view(request):
    return HttpResponse("Admissions Authorized")


@role_required(User.Role.ACCOUNTS)
def stub_accounts_view(request):
    return HttpResponse("Accounts Authorized")


class BatchManagerScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kochi = make_centre()
        cls.aluva = make_centre(code="AVM-02", name="Aluva Campus")
        cls.closed = make_centre(code="TVM-03", name="Closed Campus", is_active=False)
        cls.course = make_course()
        cls.year = make_academic_year()
        cls.kochi_batch = make_batch(cls.kochi, cls.course, cls.year, code="2026-A")
        cls.aluva_batch = make_batch(cls.aluva, cls.course, cls.year, code="2026-A")
        cls.closed_batch = make_batch(cls.closed, cls.course, cls.year, code="2026-A")

    def _batch_pks_for(self, user):
        return set(Batch.objects.for_user(user).values_list("pk", flat=True))

    def test_anonymous_user_sees_no_batches(self):
        self.assertEqual(self._batch_pks_for(AnonymousUser()), set())

    def test_none_user_sees_no_batches(self):
        self.assertEqual(self._batch_pks_for(None), set())

    def test_superuser_sees_batches_across_all_centres(self):
        superuser = make_user(
            username="superuser",
            role=User.Role.ADMISSIONS,
            is_superuser=True,
            is_staff=True,
        )
        self.assertEqual(
            self._batch_pks_for(superuser),
            {self.kochi_batch.pk, self.aluva_batch.pk, self.closed_batch.pk},
        )

    def test_admin_role_sees_all_batches_without_centre_assignments(self):
        admin_user = make_user(username="admin_user", role=User.Role.ADMIN)
        self.assertFalse(admin_user.centres.exists())
        self.assertEqual(
            self._batch_pks_for(admin_user),
            {self.kochi_batch.pk, self.aluva_batch.pk, self.closed_batch.pk},
        )

    def test_management_role_sees_all_batches_without_centre_assignments(self):
        manager = make_user(username="manager", role=User.Role.MANAGEMENT)
        self.assertFalse(manager.centres.exists())
        self.assertEqual(
            self._batch_pks_for(manager),
            {self.kochi_batch.pk, self.aluva_batch.pk, self.closed_batch.pk},
        )

    def test_explicit_cross_centre_flag_sees_all_batches(self):
        operator = make_user(
            username="cross_centre_accounts",
            role=User.Role.ACCOUNTS,
            can_access_all_centres=True,
        )
        self.assertFalse(operator.centres.exists())
        self.assertEqual(
            self._batch_pks_for(operator),
            {self.kochi_batch.pk, self.aluva_batch.pk, self.closed_batch.pk},
        )

    def test_admissions_user_only_sees_assigned_centre_batches(self):
        clerk = make_user(username="kochi_clerk", role=User.Role.ADMISSIONS)
        clerk.centres.add(self.kochi)
        self.assertEqual(self._batch_pks_for(clerk), {self.kochi_batch.pk})

    def test_accounts_user_only_sees_assigned_centre_batches(self):
        accountant = make_user(username="aluva_accountant", role=User.Role.ACCOUNTS)
        accountant.centres.add(self.aluva)
        self.assertEqual(self._batch_pks_for(accountant), {self.aluva_batch.pk})

    def test_scoped_user_with_multiple_centres_never_sees_inactive_centre_batch(self):
        clerk = make_user(username="multi_centre_clerk", role=User.Role.ADMISSIONS)
        clerk.centres.add(self.kochi, self.closed)
        self.assertEqual(self._batch_pks_for(clerk), {self.kochi_batch.pk})

    def test_user_assigned_only_to_inactive_centre_sees_no_batches(self):
        clerk = make_user(username="closed_centre_clerk", role=User.Role.ADMISSIONS)
        clerk.centres.add(self.closed)
        self.assertEqual(self._batch_pks_for(clerk), set())

    def test_user_with_no_centres_assigned_sees_no_batches(self):
        clerk = make_user(username="unassigned_clerk", role=User.Role.ADMISSIONS)
        self.assertEqual(self._batch_pks_for(clerk), set())


class RoleRequiredDecoratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = make_user(username="role_admin", role=User.Role.ADMIN)
        cls.management_user = make_user(username="role_manager", role=User.Role.MANAGEMENT)
        cls.admissions_user = make_user(username="role_admissions", role=User.Role.ADMISSIONS)
        cls.accounts_user = make_user(username="role_accounts", role=User.Role.ACCOUNTS)

    def setUp(self):
        self.factory = RequestFactory()

    def _request_for(self, user):
        request = self.factory.get("/stub-view/")
        request.user = user
        return request

    def test_anonymous_user_is_denied(self):
        with self.assertRaises(PermissionDenied):
            stub_admin_view(self._request_for(AnonymousUser()))

    def test_unauthorized_roles_are_denied(self):
        cases = (
            (self.admissions_user, stub_admin_view),
            (self.accounts_user, stub_admin_view),
            (self.admin_user, stub_admissions_view),
            (self.admissions_user, stub_accounts_view),
        )
        for user, view in cases:
            with self.subTest(user=user.username, view=view.__name__):
                with self.assertRaises(PermissionDenied):
                    view(self._request_for(user))

    def test_each_allowed_role_passes(self):
        cases = (
            (self.admin_user, stub_admin_view, b"Admin Authorized"),
            (self.management_user, stub_admin_view, b"Admin Authorized"),
            (self.admissions_user, stub_admissions_view, b"Admissions Authorized"),
            (self.accounts_user, stub_accounts_view, b"Accounts Authorized"),
        )
        for user, view, expected_body in cases:
            with self.subTest(user=user.username, view=view.__name__):
                response = view(self._request_for(user))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected_body)

    def test_superuser_passes_regardless_of_role(self):
        superuser = make_user(
            username="role_superuser",
            role=User.Role.ADMISSIONS,
            is_superuser=True,
            is_staff=True,
        )
        response = stub_admin_view(self._request_for(superuser))
        self.assertEqual(response.status_code, 200)


class CentreAccessMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kochi = make_centre()
        cls.aluva = make_centre(code="AVM-02", name="Aluva Campus")
        cls.closed = make_centre(code="TVM-03", name="Closed Campus", is_active=False)

    def test_superuser_has_all_centres_access(self):
        superuser = make_user(
            username="matrix_superuser",
            role=User.Role.ADMISSIONS,
            is_superuser=True,
            is_staff=True,
        )
        self.assertTrue(superuser.has_all_centres_access)
        self.assertTrue(superuser.can_access_centre(self.aluva))

    def test_admin_and_management_have_all_centres_access(self):
        for username, role in (
            ("matrix_admin", User.Role.ADMIN),
            ("matrix_manager", User.Role.MANAGEMENT),
        ):
            with self.subTest(role=role):
                user = make_user(username=username, role=role)
                self.assertTrue(user.has_all_centres_access)
                self.assertTrue(user.can_access_centre(self.kochi))
                self.assertFalse(user.can_access_centre(self.closed))

    def test_explicit_flag_grants_all_centres_access_to_non_admin_roles(self):
        operator = make_user(
            username="matrix_operator",
            role=User.Role.ACCOUNTS,
            can_access_all_centres=True,
        )
        self.assertTrue(operator.has_all_centres_access)
        self.assertTrue(operator.can_access_centre(self.aluva))

    def test_scoped_user_is_limited_to_assigned_active_centres(self):
        clerk = make_user(username="matrix_clerk", role=User.Role.ADMISSIONS)
        clerk.centres.add(self.kochi, self.closed)

        self.assertFalse(clerk.has_all_centres_access)
        self.assertTrue(clerk.can_access_centre(self.kochi))
        self.assertFalse(clerk.can_access_centre(self.aluva))
        self.assertFalse(clerk.can_access_centre(self.closed))
        self.assertEqual(list(clerk.get_accessible_centres()), [self.kochi])

    def test_user_without_centres_cannot_access_any_centre(self):
        clerk = make_user(username="matrix_unassigned", role=User.Role.ADMISSIONS)
        self.assertFalse(clerk.has_all_centres_access)
        self.assertFalse(clerk.can_access_centre(self.kochi))
        self.assertEqual(list(clerk.get_accessible_centres()), [])

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.centres.models import Centre
from apps.core.permissions import role_required

User = get_user_model()


@role_required(User.Role.ADMIN, User.Role.MANAGEMENT)
def sample_admin_view(request):
    return HttpResponse("Admin Authorized")


@role_required(User.Role.ACCOUNTS)
def sample_accounts_view(request):
    return HttpResponse("Accounts Authorized")


class CorePermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.centre_kochi = Centre.objects.create(code="KC-01", name="Kochi Campus")
        self.admin_user = User.objects.create_user(
            username="test_admin",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.admissions_user = User.objects.create_user(
            username="test_admissions",
            password="Password123!",
            role=User.Role.ADMISSIONS,
        )
        self.accounts_user = User.objects.create_user(
            username="test_accounts",
            password="Password123!",
            role=User.Role.ACCOUNTS,
        )

    def test_role_required_permits_authorized_role(self):
        request = self.factory.get("/dummy-admin/")
        request.user = self.admin_user
        response = sample_admin_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"Admin Authorized")

    def test_role_required_permits_superuser_regardless_of_role(self):
        superuser = User.objects.create_superuser(
            username="test_superuser",
            password="Password123!",
            role=User.Role.ADMISSIONS,
        )
        request = self.factory.get("/dummy-admin/")
        request.user = superuser
        response = sample_admin_view(request)
        self.assertEqual(response.status_code, 200)

    def test_role_required_denies_unauthorized_role(self):
        request = self.factory.get("/dummy-admin/")
        request.user = self.admissions_user
        with self.assertRaises(PermissionDenied):
            sample_admin_view(request)

    def test_role_required_denies_anonymous_user(self):
        request = self.factory.get("/dummy-admin/")
        request.user = AnonymousUser()
        with self.assertRaises(PermissionDenied):
            sample_admin_view(request)

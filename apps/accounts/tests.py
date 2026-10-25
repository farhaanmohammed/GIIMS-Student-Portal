from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.centres.models import Centre

User = get_user_model()


class UserModelTests(TestCase):
    def setUp(self):
        self.centre_kochi = Centre.objects.create(code="KC-01", name="Kochi Campus")
        self.centre_aluva = Centre.objects.create(code="AC-02", name="Aluva Campus")

    def test_create_regular_user_with_role(self):
        user = User.objects.create_user(
            username="admissions_staff",
            email="staff@giims.edu",
            password="SecurePassword123!",
            role=User.Role.ADMISSIONS,
            primary_centre=self.centre_kochi,
        )
        self.assertEqual(user.role, User.Role.ADMISSIONS)
        self.assertTrue(user.is_admissions_role)
        self.assertFalse(user.is_admin_role)
        self.assertFalse(user.is_accounts_role)
        self.assertFalse(user.is_management_role)
        self.assertEqual(user.primary_centre, self.centre_kochi)

    def test_create_superuser(self):
        admin = User.objects.create_superuser(
            username="superadmin",
            email="admin@giims.edu",
            password="SuperPassword123!",
            role=User.Role.ADMIN,
        )
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_admin_role)
        self.assertTrue(admin.has_all_centres_access)

    def test_centre_scoping_permissions(self):
        # 1. Scoped user assigned only to Kochi
        staff = User.objects.create_user(
            username="kochi_clerk",
            password="Password123!",
            role=User.Role.ADMISSIONS,
        )
        staff.centres.add(self.centre_kochi)

        self.assertFalse(staff.has_all_centres_access)
        self.assertTrue(staff.can_access_centre(self.centre_kochi))
        self.assertFalse(staff.can_access_centre(self.centre_aluva))
        accessible = list(staff.get_accessible_centres())
        self.assertEqual(accessible, [self.centre_kochi])

        # 2. Cross-centre management user
        manager = User.objects.create_user(
            username="regional_manager",
            password="Password123!",
            role=User.Role.MANAGEMENT,
        )
        self.assertTrue(manager.has_all_centres_access)
        self.assertTrue(manager.can_access_centre(self.centre_kochi))
        self.assertTrue(manager.can_access_centre(self.centre_aluva))
        all_centres = list(manager.get_accessible_centres().order_by("code"))
        self.assertEqual(all_centres, [self.centre_aluva, self.centre_kochi])

        # 3. Explicit cross-centre permission flag on staff
        staff.can_access_all_centres = True
        staff.save()
        self.assertTrue(staff.has_all_centres_access)
        self.assertTrue(staff.can_access_centre(self.centre_aluva))

    def test_can_access_centre_accepts_primary_key_and_code(self):
        manager = User.objects.create_user(
            username="pk_manager",
            password="Password123!",
            role=User.Role.MANAGEMENT,
        )
        self.assertTrue(manager.can_access_centre(self.centre_kochi.pk))
        self.assertTrue(manager.can_access_centre("KC-01"))
        self.assertFalse(manager.can_access_centre(999999))
        self.assertFalse(manager.can_access_centre("UNKNOWN"))
        self.assertFalse(manager.can_access_centre(None))

    def test_can_access_centre_rejects_inactive_centre_by_pk_and_code(self):
        user = User.objects.create_user(
            username="inactive_scope_admin",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.centre_kochi.is_active = False
        self.centre_kochi.save()

        self.assertFalse(user.can_access_centre(self.centre_kochi))
        self.assertFalse(user.can_access_centre(self.centre_kochi.pk))
        self.assertFalse(user.can_access_centre("KC-01"))


class AuthenticationFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="portal_operator",
            password="ValidOperatorPass123!",
            role=User.Role.ACCOUNTS,
        )

    def test_login_page_renders_cleanly(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GIIMS Portal Login")

    def test_login_successful_redirect(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "portal_operator",
                "password": "ValidOperatorPass123!",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_login_invalid_credentials_shows_error(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "portal_operator",
                "password": "WrongPassword!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Authentication failed")

    def test_logout_terminates_session(self):
        self.client.login(username="portal_operator", password="ValidOperatorPass123!")
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

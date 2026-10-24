from django.db import IntegrityError
from django.test import TestCase

from apps.centres.models import Centre


class CentreModelTests(TestCase):
    def setUp(self):
        self.centre_kochi = Centre.objects.create(
            code="KC-01",
            name="Kochi Main Campus",
            address="Kochi, Kerala",
            email="kochi@giims.edu",
            phone="+91 484 2000001",
            is_active=True,
        )

    def test_centre_creation_and_str(self):
        self.assertEqual(str(self.centre_kochi), "Kochi Main Campus (KC-01)")
        self.assertTrue(self.centre_kochi.is_active)
        self.assertIsNotNone(self.centre_kochi.created_at)
        self.assertIsNotNone(self.centre_kochi.updated_at)

    def test_centre_code_uniqueness(self):
        with self.assertRaises(IntegrityError):
            Centre.objects.create(
                code="KC-01",  # Duplicate code
                name="Duplicate Campus",
            )

    def test_centre_ordering(self):
        Centre.objects.create(code="AC-02", name="Aluva Campus")
        Centre.objects.create(code="ZC-03", name="Z-Campus")
        codes = list(Centre.objects.values_list("code", flat=True))
        self.assertEqual(codes, ["AC-02", "KC-01", "ZC-03"])

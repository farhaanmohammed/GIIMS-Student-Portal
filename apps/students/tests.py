from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.centres.models import Centre
from apps.academics.models import AcademicYear, Course, Batch
from apps.students.models import Student
from apps.admissions.models import Admission
from apps.admissions.services import enroll_admission


class StudentIdGenerationTest(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TVM", name="Trivandrum", is_active=True)
        self.ay = AcademicYear.objects.create(
            label="2026-2027",
            start_date=timezone.localdate(),
            end_date=timezone.localdate().replace(year=2027),
            is_active=True,
        )
        self.course = Course.objects.create(code="AD", name="Aviation", duration_months=12, is_active=True)
        self.batch = Batch.objects.create(
            centre=self.centre,
            course=self.course,
            academic_year=self.ay,
            code="ASP",
            start_date=timezone.localdate(),
            end_date=timezone.localdate().replace(year=2027),
            is_active=True,
        )
        self.user = User.objects.create_user(username="admin", password="pass", role="ADMIN")

    def test_student_id_generated_on_enrollment_with_correct_format(self):
        student = Student.objects.create(
            first_name="John",
            last_name="Doe",
            date_of_birth="2000-01-01",
            gender="MALE",
            phone="1234567890",
            address_line1="Addr",
            city="City",
            state="State",
            pincode="123456",
            guardian_name="Parent",
            guardian_phone="1234567891",
            guardian_relation="Father",
        )
        admission = Admission.objects.create(
            student=student,
            centre=self.centre,
            course=self.course,
            batch=self.batch,
            academic_year=self.ay,
            status=Admission.Status.ENQUIRED,
        )
        enroll_admission(admission, self.user)
        student.refresh_from_db()
        self.assertEqual(student.student_id, "TVM-ASP-AD_001")

    def test_student_id_never_changes_after_generation(self):
        student = Student.objects.create(
            first_name="Jane",
            last_name="Doe",
            date_of_birth="2000-01-01",
            gender="FEMALE",
            phone="1234567892",
            address_line1="Addr",
            city="City",
            state="State",
            pincode="123456",
            guardian_name="Parent",
            guardian_phone="1234567893",
            guardian_relation="Mother",
        )
        admission = Admission.objects.create(
            student=student,
            centre=self.centre,
            course=self.course,
            batch=self.batch,
            academic_year=self.ay,
            status=Admission.Status.ENQUIRED,
        )
        enroll_admission(admission, self.user)
        student.refresh_from_db()
        old_id = student.student_id
        course2 = Course.objects.create(code="HRM", name="HRM", duration_months=12, is_active=True)
        batch2 = Batch.objects.create(
            centre=self.centre,
            course=course2,
            academic_year=self.ay,
            code="BSP",
            start_date=timezone.localdate(),
            end_date=timezone.localdate().replace(year=2027),
            is_active=True,
        )
        admission2 = Admission.objects.create(
            student=student,
            centre=self.centre,
            course=course2,
            batch=batch2,
            academic_year=self.ay,
            status=Admission.Status.ENQUIRED,
        )
        enroll_admission(admission2, self.user)
        student.refresh_from_db()
        self.assertEqual(student.student_id, old_id)


class Phase3PermissionsTest(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TVM", name="Trivandrum", is_active=True)
        self.other_centre = Centre.objects.create(code="KOC", name="Kochi", is_active=True)
        self.ay = AcademicYear.objects.create(
            label="2026-2027",
            start_date=timezone.localdate(),
            end_date=timezone.localdate().replace(year=2027),
            is_active=True,
        )
        self.course = Course.objects.create(code="AD", name="Aviation", duration_months=12, is_active=True)
        self.batch = Batch.objects.create(
            centre=self.centre,
            course=self.course,
            academic_year=self.ay,
            code="ASP",
            start_date=timezone.localdate(),
            end_date=timezone.localdate().replace(year=2027),
            is_active=True,
        )
        self.admissions_user = User.objects.create_user(username="admissions", password="pass", role="ADMISSIONS")
        self.admissions_user.centres.add(self.centre)
        self.accounts_user = User.objects.create_user(username="accounts", password="pass", role="ACCOUNTS")
        self.accounts_user.centres.add(self.centre)

    def test_only_admissions_admin_management_can_create_enquiry(self):
        self.client.login(username="accounts", password="pass")
        resp = self.client.get(reverse("admissions:enquiry_create"))
        self.assertEqual(resp.status_code, 302)

    def test_enrollment_requires_permission(self):
        student = Student.objects.create(
            first_name="Test",
            last_name="User",
            date_of_birth="2000-01-01",
            gender="MALE",
            phone="9999999999",
            address_line1="Addr",
            city="City",
            state="State",
            pincode="123456",
            guardian_name="Parent",
            guardian_phone="9999999998",
            guardian_relation="Father",
        )
        admission = Admission.objects.create(
            student=student,
            centre=self.centre,
            course=self.course,
            batch=self.batch,
            academic_year=self.ay,
            status=Admission.Status.ENQUIRED,
        )
        self.client.login(username="accounts", password="pass")
        resp = self.client.post(reverse("admissions:enroll", args=[admission.pk]))
        self.assertEqual(resp.status_code, 302)
        admission.refresh_from_db()
        self.assertEqual(admission.status, Admission.Status.ENQUIRED)

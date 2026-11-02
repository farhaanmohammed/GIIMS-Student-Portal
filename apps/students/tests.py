from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Batch, Course
from apps.accounts.models import User
from apps.admissions.models import Admission
from apps.admissions.services import enroll_admission
from apps.centres.models import Centre
from apps.students.models import Student, StudentIdSequence
from apps.students.services import (
    build_student_id_prefix,
    format_student_id,
    generate_student_id_for_enrollment,
)


def create_student(**overrides) -> Student:
    """Build a synthetic student record for tests."""
    data = {
        "first_name": "Test",
        "last_name": "Student",
        "date_of_birth": "2000-01-01",
        "gender": "MALE",
        "phone": "9000000000",
        "address_line1": "1 Test Street",
        "city": "City",
        "state": "State",
        "pincode": "695001",
        "guardian_name": "Test Guardian",
        "guardian_phone": "9000000001",
        "guardian_relation": "Father",
    }
    data.update(overrides)
    return Student.objects.create(**data)


class Phase3TestBase(TestCase):
    """Shared synthetic masters for Phase 3 tests."""

    def setUp(self):
        today = timezone.localdate()
        next_year = today.replace(year=today.year + 1)

        self.centre = Centre.objects.create(code="TVM", name="Trivandrum", is_active=True)
        self.other_centre = Centre.objects.create(code="KOC", name="Kochi", is_active=True)
        self.academic_year = AcademicYear.objects.create(
            label="2026-2027", start_date=today, end_date=next_year, is_active=True
        )
        self.course = Course.objects.create(
            code="AD", name="Aviation", duration_months=12, is_active=True
        )
        self.batch = Batch.objects.create(
            centre=self.centre,
            course=self.course,
            academic_year=self.academic_year,
            code="ASP",
            start_date=today,
            end_date=next_year,
            is_active=True,
        )
        self.admin = User.objects.create_superuser(
            username="admin", password="pass", role=User.Role.ADMIN
        )
        self.admissions_user = User.objects.create_user(
            username="admissions", password="pass", role=User.Role.ADMISSIONS
        )
        self.admissions_user.centres.add(self.centre)
        self.accounts_user = User.objects.create_user(
            username="accounts", password="pass", role=User.Role.ACCOUNTS
        )
        self.accounts_user.centres.add(self.centre)

    def make_admission(self, student=None, *, status=Admission.Status.ENQUIRED, batch=None):
        return Admission.objects.create(
            student=student or create_student(),
            centre=self.centre,
            course=self.course,
            batch=batch or self.batch,
            academic_year=self.academic_year,
            status=status,
        )


class StudentIdGenerationTests(Phase3TestBase):
    def test_format_is_centre_batch_course_with_three_digit_suffix(self):
        admission = self.make_admission()

        enroll_admission(admission, self.admin)

        admission.student.refresh_from_db()
        self.assertEqual(admission.student.student_id, "TVM-ASP-AD_001")

    def test_sequence_increments_for_same_prefix(self):
        first = self.make_admission()
        second = self.make_admission()

        enroll_admission(first, self.admin)
        enroll_admission(second, self.admin)

        first.student.refresh_from_db()
        second.student.refresh_from_db()
        self.assertEqual(first.student.student_id, "TVM-ASP-AD_001")
        self.assertEqual(second.student.student_id, "TVM-ASP-AD_002")

    def test_id_is_not_assigned_before_enrollment(self):
        admission = self.make_admission()

        admission.student.refresh_from_db()
        self.assertIsNone(admission.student.student_id)

    def test_id_never_changes_when_student_changes_course(self):
        admission = self.make_admission()
        enroll_admission(admission, self.admin)
        admission.student.refresh_from_db()
        original_id = admission.student.student_id

        second_course = Course.objects.create(
            code="HRM", name="HRM", duration_months=12, is_active=True
        )
        second_batch = Batch.objects.create(
            centre=self.centre,
            course=second_course,
            academic_year=self.academic_year,
            code="BSP",
            start_date=self.batch.start_date,
            end_date=self.batch.end_date,
            is_active=True,
        )
        second_admission = Admission.objects.create(
            student=admission.student,
            centre=self.centre,
            course=second_course,
            batch=second_batch,
            academic_year=self.academic_year,
            status=Admission.Status.ENQUIRED,
        )

        enroll_admission(second_admission, self.admin)

        admission.student.refresh_from_db()
        self.assertEqual(admission.student.student_id, original_id)

    def test_generator_is_idempotent_for_already_assigned_student(self):
        admission = self.make_admission()
        enroll_admission(admission, self.admin)

        result = generate_student_id_for_enrollment(admission)

        admission.student.refresh_from_db()
        self.assertEqual(result, "TVM-ASP-AD_001")
        self.assertEqual(admission.student.student_id, "TVM-ASP-AD_001")

    def test_student_id_is_globally_unique(self):
        first = self.make_admission()
        enroll_admission(first, self.admin)
        first.student.refresh_from_db()
        assigned_id = first.student.student_id
        second = self.make_admission()

        with self.assertRaises(IntegrityError):
            Student.objects.create(
                student_id=assigned_id,
                first_name="Dup",
                last_name="Id",
                date_of_birth="2000-01-01",
                gender="MALE",
                phone="9000000002",
                address_line1="Addr",
                city="City",
                state="State",
                pincode="695002",
                guardian_name="Guardian",
                guardian_phone="9000000003",
                guardian_relation="Mother",
            )
        self.assertIsNone(second.student.student_id)


class StudentIdSequenceTests(Phase3TestBase):
    """The counter row is the concurrency-safe source of student ID numbers."""

    def test_prefix_combines_centre_batch_and_course_codes(self):
        admission = self.make_admission()

        self.assertEqual(build_student_id_prefix(admission), "TVM-ASP-AD")

    def test_sequence_row_is_created_and_advanced(self):
        first = self.make_admission()
        second = self.make_admission()

        enroll_admission(first, self.admin)
        enroll_admission(second, self.admin)

        sequence = StudentIdSequence.objects.get(prefix="TVM-ASP-AD")
        self.assertEqual(sequence.last_number, 2)

    def test_numbers_continue_when_student_id_already_exists(self):
        # Simulates pre-existing data created before the counter table.
        Student.objects.create(
            student_id="TVM-ASP-AD_001",
            first_name="Legacy",
            last_name="Record",
            date_of_birth="2000-01-01",
            gender="MALE",
            phone="9000000010",
            address_line1="Addr",
            city="City",
            state="State",
            pincode="695003",
            guardian_name="Guardian",
            guardian_phone="9000000011",
            guardian_relation="Mother",
        )
        admission = self.make_admission()

        enroll_admission(admission, self.admin)

        admission.student.refresh_from_db()
        self.assertEqual(admission.student.student_id, "TVM-ASP-AD_002")

    def test_format_pads_to_three_digits_and_grows_beyond(self):
        self.assertEqual(format_student_id("TVM-ASP-AD", 1), "TVM-ASP-AD_001")
        self.assertEqual(format_student_id("TVM-ASP-AD", 42), "TVM-ASP-AD_042")
        self.assertEqual(format_student_id("TVM-ASP-AD", 1234), "TVM-ASP-AD_1234")


class EnrollmentServiceTests(Phase3TestBase):
    def test_enrollment_sets_status_actor_and_timestamp(self):
        admission = self.make_admission()

        enroll_admission(admission, self.admissions_user)

        admission.refresh_from_db()
        self.assertEqual(admission.status, Admission.Status.ENROLLED)
        self.assertEqual(admission.enrolled_by, self.admissions_user)
        self.assertIsNotNone(admission.enrolled_at)

    def test_enrollment_rejects_non_enquired_admission(self):
        admission = self.make_admission(status=Admission.Status.DROPPED)

        with self.assertRaises(ValueError):
            enroll_admission(admission, self.admin)

        admission.refresh_from_db()
        self.assertEqual(admission.status, Admission.Status.DROPPED)


class AdmissionPermissionTests(Phase3TestBase):
    def test_accounts_role_cannot_open_enquiry_form(self):
        self.client.login(username="accounts", password="pass")

        response = self.client.get(reverse("admissions:enquiry_create"))

        self.assertRedirects(response, reverse("admissions:admission_list"))

    def test_accounts_role_cannot_enroll(self):
        admission = self.make_admission()
        self.client.login(username="accounts", password="pass")

        self.client.post(reverse("admissions:enroll", args=[admission.pk]))

        admission.refresh_from_db()
        self.assertEqual(admission.status, Admission.Status.ENQUIRED)
        self.assertIsNone(admission.student.student_id)

    def test_admissions_role_can_enroll_accessible_centre_admission(self):
        admission = self.make_admission()
        self.client.login(username="admissions", password="pass")

        self.client.post(reverse("admissions:enroll", args=[admission.pk]))

        admission.refresh_from_db()
        self.assertEqual(admission.status, Admission.Status.ENROLLED)
        self.assertEqual(admission.student.student_id, "TVM-ASP-AD_001")

    def test_enrollment_requires_post(self):
        admission = self.make_admission()
        self.client.login(username="admin", password="pass")

        response = self.client.get(reverse("admissions:enroll", args=[admission.pk]))

        self.assertEqual(response.status_code, 405)

    def test_anonymous_user_is_redirected_to_login(self):
        admission = self.make_admission()

        response = self.client.post(reverse("admissions:enroll", args=[admission.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

"""Synthetic factories for fee tests. No real personal or financial data."""

from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.academics.models import AcademicYear, Batch, Course
from apps.admissions.models import Admission
from apps.centres.models import Centre
from apps.fees.models import FeeInstallment, FeePlan
from apps.fees.services import approve_fee_plan, create_draft_fee_plan
from apps.students.models import Student

User = get_user_model()

# Synthetic test credential, not a real secret.
TEST_PASSWORD = "SyntheticTestPass123!"

COURSE_FEE = Decimal("167500.00")
GST_RATE = Decimal("18.00")
GST_AMOUNT = Decimal("30150.00")
TOTAL_PAYABLE = Decimal("197650.00")

COURSE_DUE_DATES = (
    date(2026, 7, 15),
    date(2026, 10, 15),
    date(2027, 1, 15),
    date(2027, 4, 15),
)


def make_centre(code: str = "TVM", name: str = "Trivandrum", **overrides: Any) -> Centre:
    return Centre.objects.create(code=code, name=name, **overrides)


def make_user(
    username: str,
    *,
    role: str = User.Role.ADMIN,
    password: str = TEST_PASSWORD,
    **overrides: Any,
):
    return User.objects.create_user(username=username, password=password, role=role, **overrides)


def make_student(**overrides: Any) -> Student:
    data: dict[str, Any] = {
        "first_name": "Test",
        "last_name": "Student",
        "date_of_birth": date(2000, 1, 1),
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


def default_components() -> list[dict[str, Any]]:
    """Application 1,000 + admission 10,000 + four course rows = 1,97,650."""
    return [
        {
            "sequence": 1,
            "category": FeeInstallment.Category.APPLICATION,
            "description": "Application Fee",
            "amount": Decimal("1000.00"),
        },
        {
            "sequence": 2,
            "category": FeeInstallment.Category.ADMISSION,
            "description": "Admission Fee",
            "amount": Decimal("10000.00"),
        },
        {
            "sequence": 3,
            "category": FeeInstallment.Category.COURSE,
            "description": "Course Installment 1",
            "amount": Decimal("40000.00"),
            "original_due_date": COURSE_DUE_DATES[0],
        },
        {
            "sequence": 4,
            "category": FeeInstallment.Category.COURSE,
            "description": "Course Installment 2",
            "amount": Decimal("50000.00"),
            "original_due_date": COURSE_DUE_DATES[1],
        },
        {
            "sequence": 5,
            "category": FeeInstallment.Category.COURSE,
            "description": "Course Installment 3",
            "amount": Decimal("50000.00"),
            "original_due_date": COURSE_DUE_DATES[2],
        },
        {
            "sequence": 6,
            "category": FeeInstallment.Category.COURSE,
            "description": "Course Installment 4",
            "amount": Decimal("46650.00"),
            "original_due_date": COURSE_DUE_DATES[3],
        },
    ]


class FeeTestBase(TestCase):
    """Shared masters, users, admission, and fee-plan helpers."""

    def setUp(self):
        today = timezone.localdate()
        self.centre = make_centre()
        self.other_centre = make_centre(code="KOC", name="Kochi")
        self.academic_year = AcademicYear.objects.create(
            label="2026-2027",
            start_date=today,
            end_date=today.replace(year=today.year + 1),
            is_active=True,
        )
        self.course = Course.objects.create(
            code="LSC", name="Logistics & SCM", duration_months=12, is_active=True
        )
        self.batch = Batch.objects.create(
            centre=self.centre,
            course=self.course,
            academic_year=self.academic_year,
            code="JUL26",
            start_date=today,
            end_date=today.replace(year=today.year + 1),
            is_active=True,
        )

        self.admin = User.objects.create_superuser(
            username="admin", password=TEST_PASSWORD, role=User.Role.ADMIN
        )
        self.management = make_user("management", role=User.Role.MANAGEMENT)
        self.accounts = make_user("accounts", role=User.Role.ACCOUNTS)
        self.accounts.centres.add(self.centre)
        self.admissions = make_user("admissions", role=User.Role.ADMISSIONS)
        self.admissions.centres.add(self.centre)
        self.other_admissions = make_user("other_admissions", role=User.Role.ADMISSIONS)
        self.other_admissions.centres.add(self.other_centre)

    def make_admission(
        self,
        *,
        centre: Centre | None = None,
        status: str = Admission.Status.ENROLLED,
        student: Student | None = None,
        course: Course | None = None,
        batch: Batch | None = None,
    ) -> Admission:
        return Admission.objects.create(
            student=student or make_student(),
            centre=centre or self.centre,
            course=course or self.course,
            batch=batch or self.batch,
            academic_year=self.academic_year,
            status=status,
        )

    def make_draft_plan(
        self,
        *,
        admission: Admission | None = None,
        course_fee=COURSE_FEE,
        gst_rate=GST_RATE,
        components=None,
        actor=None,
        notes="",
    ) -> FeePlan:
        return create_draft_fee_plan(
            admission or self.make_admission(),
            course_fee=course_fee,
            gst_rate=gst_rate,
            components=default_components() if components is None else components,
            actor=actor or self.admin,
            notes=notes,
        )

    def make_approved_plan(self, **kwargs) -> FeePlan:
        plan = self.make_draft_plan(**kwargs)
        return approve_fee_plan(plan, kwargs.get("actor") or self.admin)

    def make_plan_row(self, *, admission: Admission | None = None, **overrides) -> FeePlan:
        """Create a plan directly, for database-constraint tests."""
        data: dict[str, Any] = {
            "admission": admission or self.make_admission(),
            "version": 1,
            "status": FeePlan.Status.DRAFT,
            "course_fee": Decimal("0.00"),
            "gst_rate": Decimal("0.00"),
            "gst_amount": Decimal("0.00"),
            "total_payable": Decimal("0.00"),
        }
        data.update(overrides)
        if data["status"] in {FeePlan.Status.APPROVED, FeePlan.Status.SUPERSEDED}:
            data.setdefault("approved_at", timezone.now())
            data.setdefault("approved_by", self.admin)
        return FeePlan.objects.create(**data)

    def course_installments(self, plan: FeePlan):
        return list(
            plan.installments.filter(category=FeeInstallment.Category.COURSE).order_by("sequence")
        )

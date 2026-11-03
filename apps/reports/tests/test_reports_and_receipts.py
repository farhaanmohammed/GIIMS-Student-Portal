from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, Batch, Course
from apps.admissions.models import Admission
from apps.centres.models import Centre
from apps.fees.models import FeeInstallment
from apps.fees.services import FeeComponent, approve_fee_plan, create_draft_fee_plan
from apps.payments.models import AdvanceCredit, FinancialAuditEvent, Payment, PaymentMode, Receipt
from apps.payments.services import (
    decide_payment_reversal,
    decide_refund,
    record_payment,
    request_payment_reversal,
    request_refund,
)
from apps.reports.selectors import (
    build_adjustments_report,
    build_collection_report,
    build_outstanding_report,
    build_overdue_installments_report,
    build_student_ledger,
    dashboard_summary,
)
from apps.students.models import Student

User = get_user_model()


def _six_components(
    *,
    app_amt: Decimal = Decimal("1000.00"),
    adm_amt: Decimal = Decimal("10000.00"),
    c1: tuple[Decimal, date] = (Decimal("25000.00"), date(2026, 5, 1)),
    c2: tuple[Decimal, date] = (Decimal("25000.00"), date(2026, 8, 1)),
    c3: tuple[Decimal, date] = (Decimal("25000.00"), date(2026, 11, 1)),
    c4: tuple[Decimal, date] = (Decimal("32000.00"), date(2027, 2, 1)),
) -> list[FeeComponent]:
    return [
        FeeComponent(
            sequence=1,
            category=FeeInstallment.Category.APPLICATION,
            description="Application Fee",
            amount=app_amt,
        ),
        FeeComponent(
            sequence=2,
            category=FeeInstallment.Category.ADMISSION,
            description="Admission Fee",
            amount=adm_amt,
        ),
        FeeComponent(
            sequence=3,
            category=FeeInstallment.Category.COURSE,
            description="Term 1 Tuition",
            amount=c1[0],
            original_due_date=c1[1],
        ),
        FeeComponent(
            sequence=4,
            category=FeeInstallment.Category.COURSE,
            description="Term 2 Tuition",
            amount=c2[0],
            original_due_date=c2[1],
        ),
        FeeComponent(
            sequence=5,
            category=FeeInstallment.Category.COURSE,
            description="Term 3 Tuition",
            amount=c3[0],
            original_due_date=c3[1],
        ),
        FeeComponent(
            sequence=6,
            category=FeeInstallment.Category.COURSE,
            description="Term 4 Tuition",
            amount=c4[0],
            original_due_date=c4[1],
        ),
    ]


class Phase6ReportsAndReceiptsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.centre_kochi = Centre.objects.create(code="KC", name="Kochi Campus")
        cls.centre_calicut = Centre.objects.create(code="CL", name="Calicut Campus")

        cls.admin_user = User.objects.create_user(
            username="admin_p6",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        cls.management_user = User.objects.create_user(
            username="mgmt_p6",
            password="Password123!",
            role=User.Role.MANAGEMENT,
        )
        cls.accounts_kochi = User.objects.create_user(
            username="acc_kochi_p6",
            password="Password123!",
            role=User.Role.ACCOUNTS,
            primary_centre=cls.centre_kochi,
        )
        cls.accounts_kochi.centres.add(cls.centre_kochi)
        cls.accounts_calicut = User.objects.create_user(
            username="acc_calicut_p6",
            password="Password123!",
            role=User.Role.ACCOUNTS,
            primary_centre=cls.centre_calicut,
        )
        cls.accounts_calicut.centres.add(cls.centre_calicut)
        cls.admissions_kochi = User.objects.create_user(
            username="adm_kochi_p6",
            password="Password123!",
            role=User.Role.ADMISSIONS,
            primary_centre=cls.centre_kochi,
        )
        cls.admissions_kochi.centres.add(cls.centre_kochi)

        cls.year = AcademicYear.objects.create(
            label="2026-2027",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_active=True,
        )
        cls.course = Course.objects.create(
            code="BBA",
            name="BBA Aviation",
            duration_months=36,
        )
        cls.batch_kochi = Batch.objects.create(
            centre=cls.centre_kochi,
            course=cls.course,
            academic_year=cls.year,
            code="BBA-KC-01",
            start_date=date(2026, 6, 1),
            end_date=date(2029, 5, 31),
            capacity=40,
        )
        cls.batch_calicut = Batch.objects.create(
            centre=cls.centre_calicut,
            course=cls.course,
            academic_year=cls.year,
            code="BBA-CL-01",
            start_date=date(2026, 6, 1),
            end_date=date(2029, 5, 31),
            capacity=40,
        )

        cls.student_kochi = Student.objects.create(
            student_id="KC-BBA-KC-01-BBA_001",
            first_name="Rahul",
            last_name="Nair",
            date_of_birth=date(2005, 5, 10),
            gender=Student.Gender.MALE,
            phone="+919876543210",
            email="rahul@example.com",
            address_line1="12 MG Road",
            city="Kochi",
            state="Kerala",
            pincode="682001",
            guardian_name="Suresh Nair",
            guardian_phone="+919876500001",
            guardian_relation="Father",
        )
        cls.admission_kochi = Admission.objects.create(
            student=cls.student_kochi,
            centre=cls.centre_kochi,
            course=cls.course,
            academic_year=cls.year,
            batch=cls.batch_kochi,
            status=Admission.Status.ENROLLED,
        )

        cls.student_calicut = Student.objects.create(
            student_id="CL-BBA-CL-01-BBA_001",
            first_name="Ananya",
            last_name="Menon",
            date_of_birth=date(2005, 8, 19),
            gender=Student.Gender.FEMALE,
            phone="+919876543211",
            email="ananya@example.com",
            address_line1="45 Mavoor Road",
            city="Calicut",
            state="Kerala",
            pincode="673001",
            guardian_name="Rajesh Menon",
            guardian_phone="+919876500002",
            guardian_relation="Father",
        )
        cls.admission_calicut = Admission.objects.create(
            student=cls.student_calicut,
            centre=cls.centre_calicut,
            course=cls.course,
            academic_year=cls.year,
            batch=cls.batch_calicut,
            status=Admission.Status.ENROLLED,
        )

        # Create and approve fee plan for Kochi admission (course_fee=100,000 + 18% GST = 118,000)
        # Installment 3 (Term 1 Tuition, 25,000) is due 2026-05-01 (overdue as of 2026-06-15)
        cls.plan_kochi = create_draft_fee_plan(
            cls.admission_kochi,
            course_fee=Decimal("100000.00"),
            gst_rate=Decimal("18.00"),
            components=_six_components(),
            actor=cls.accounts_kochi,
        )
        approve_fee_plan(cls.plan_kochi, cls.admin_user)
        cls.kochi_installments = list(cls.plan_kochi.installments.order_by("sequence"))
        cls.inst_kochi_app = cls.kochi_installments[0]  # 1,000
        cls.inst_kochi_adm = cls.kochi_installments[1]  # 10,000
        cls.inst_kochi_c1 = cls.kochi_installments[2]  # 25,000 due 2026-05-01

        # Record a payment of 26,000 allocated to Application (1,000), Admission (10,000),
        # and Term 1 Tuition (15,000), leaving 10,000 overdue on Term 1 Tuition as of 2026-06-15.
        cls.payment_kochi = record_payment(
            cls.admission_kochi,
            amount=Decimal("26000.00"),
            paid_on=date(2026, 5, 10),
            mode=PaymentMode.UPI,
            collection_centre=cls.centre_kochi,
            actor=cls.accounts_kochi,
            reference="=UPI-REF-001",  # starts with '=' to test CSV formula neutralization
            allocations=[
                {"installment": cls.inst_kochi_app, "amount": Decimal("1000.00")},
                {"installment": cls.inst_kochi_adm, "amount": Decimal("10000.00")},
                {"installment": cls.inst_kochi_c1, "amount": Decimal("15000.00")},
            ],
        )

        # Create and approve fee plan for Calicut admission (total 118,000)
        cls.plan_calicut = create_draft_fee_plan(
            cls.admission_calicut,
            course_fee=Decimal("100000.00"),
            gst_rate=Decimal("18.00"),
            components=_six_components(),
            actor=cls.accounts_calicut,
        )
        approve_fee_plan(cls.plan_calicut, cls.admin_user)
        cls.calicut_installments = list(cls.plan_calicut.installments.order_by("sequence"))
        cls.payment_calicut = record_payment(
            cls.admission_calicut,
            amount=Decimal("11000.00"),
            paid_on=date(2026, 5, 12),
            mode=PaymentMode.BANK_TRANSFER,
            collection_centre=cls.centre_calicut,
            actor=cls.accounts_calicut,
            reference="NEFT-CL-01",
            allocations=[
                {"installment": cls.calicut_installments[0], "amount": Decimal("1000.00")},
                {"installment": cls.calicut_installments[1], "amount": Decimal("10000.00")},
            ],
        )

    def test_receipt_print_and_pdf_preserve_number_and_log_audit_event(self):
        original_number = self.payment_kochi.receipt.number
        original_payment_count = Payment.objects.count()
        original_receipt_count = Receipt.objects.count()

        self.client.force_login(self.accounts_kochi)

        # 1. Open printable HTML receipt view
        print_url = reverse("payments:receipt_print", args=[self.payment_kochi.pk])
        response = self.client.get(print_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, original_number)
        self.assertContains(response, "Rahul Nair")

        # 2. Download PDF receipt
        pdf_url = reverse("payments:receipt_pdf", args=[self.payment_kochi.pk])
        pdf_response = self.client.get(pdf_url)
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF-1.4\n"))
        self.assertIn(original_number.encode("latin-1"), pdf_response.content)

        # Verify no duplicate payment or receipt was created and receipt number is unchanged
        self.payment_kochi.receipt.refresh_from_db()
        self.assertEqual(self.payment_kochi.receipt.number, original_number)
        self.assertEqual(Payment.objects.count(), original_payment_count)
        self.assertEqual(Receipt.objects.count(), original_receipt_count)

        # Verify two RECEIPT_REPRINTED audit events were recorded (HTML and PDF)
        reprint_events = list(
            FinancialAuditEvent.objects.filter(
                payment=self.payment_kochi,
                event_type=FinancialAuditEvent.Event.RECEIPT_REPRINTED,
            ).order_by("pk")
        )
        self.assertEqual(len(reprint_events), 2)
        self.assertIn("as HTML", reprint_events[0].detail)
        self.assertIn("as PDF", reprint_events[1].detail)
        self.assertEqual(reprint_events[0].actor, self.accounts_kochi)

    def test_reversed_payment_receipt_shows_void_banner_in_html_and_pdf(self):
        reversal = request_payment_reversal(
            self.payment_kochi,
            reason="Cheque dishonoured by bank",
            actor=self.accounts_kochi,
            request_key="rev-kochi-001",
        )
        decide_payment_reversal(
            reversal,
            approve=True,
            actor=self.admin_user,
            note="Confirmed bounce",
        )

        self.client.force_login(self.accounts_kochi)
        html_res = self.client.get(reverse("payments:receipt_print", args=[self.payment_kochi.pk]))
        self.assertEqual(html_res.status_code, 200)
        self.assertContains(html_res, "REVERSED / VOID")
        self.assertContains(html_res, "VOID NOTICE:")

        pdf_res = self.client.get(reverse("payments:receipt_pdf", args=[self.payment_kochi.pk]))
        self.assertEqual(pdf_res.status_code, 200)
        self.assertIn(b"REVERSED / VOID", pdf_res.content)

    def test_receipt_print_and_pdf_enforce_role_and_centre_scope(self):
        print_url = reverse("payments:receipt_print", args=[self.payment_kochi.pk])
        pdf_url = reverse("payments:receipt_pdf", args=[self.payment_kochi.pk])

        # ADMISSIONS role is denied (403)
        self.client.force_login(self.admissions_kochi)
        self.assertEqual(self.client.get(print_url).status_code, 403)
        self.assertEqual(self.client.get(pdf_url).status_code, 403)

        # ACCOUNTS user from Calicut gets 404 on Kochi receipt
        self.client.force_login(self.accounts_calicut)
        self.assertEqual(self.client.get(print_url).status_code, 404)
        self.assertEqual(self.client.get(pdf_url).status_code, 404)

    def test_student_ledger_selector_and_role_permissions(self):
        ledger = build_student_ledger(self.admission_kochi, as_of=date(2026, 6, 15))
        self.assertEqual(ledger.summary.total_billed, Decimal("118000.00"))
        self.assertEqual(ledger.summary.total_paid, Decimal("26000.00"))
        self.assertEqual(ledger.summary.total_outstanding, Decimal("92000.00"))
        self.assertEqual(ledger.summary.overdue_outstanding, Decimal("10000.00"))
        self.assertEqual(len(ledger.installment_rows), 6)
        self.assertEqual(len(ledger.payments), 1)

        ledger_html_url = reverse("reports:student_ledger", args=[self.admission_kochi.pk])
        ledger_csv_url = reverse("reports:student_ledger_csv", args=[self.admission_kochi.pk])
        ledger_pdf_url = reverse("reports:student_ledger_pdf", args=[self.admission_kochi.pk])

        # ADMISSIONS can view HTML ledger for accessible centre (404 other, 403 exports)
        self.client.force_login(self.admissions_kochi)
        self.assertEqual(self.client.get(ledger_html_url).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("reports:student_ledger", args=[self.admission_calicut.pk])
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(ledger_csv_url).status_code, 403)
        self.assertEqual(self.client.get(ledger_pdf_url).status_code, 403)

        # ACCOUNTS can view HTML and export CSV + PDF for accessible centre, 404 for other centre
        self.client.force_login(self.accounts_kochi)
        self.assertEqual(self.client.get(ledger_html_url).status_code, 200)
        csv_res = self.client.get(ledger_csv_url)
        self.assertEqual(csv_res.status_code, 200)
        self.assertEqual(csv_res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Rahul Nair", csv_res.content.decode("utf-8"))

        pdf_res = self.client.get(ledger_pdf_url)
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res["Content-Type"], "application/pdf")
        self.assertTrue(pdf_res.content.startswith(b"%PDF-1.4\n"))

        self.assertEqual(
            self.client.get(
                reverse("reports:student_ledger_csv", args=[self.admission_calicut.pk])
            ).status_code,
            404,
        )

    def test_outstanding_and_overdue_reports_respect_centre_scope_and_as_of_date(self):
        # Kochi user sees only Kochi admission in outstanding report
        kochi_out = build_outstanding_report(self.accounts_kochi, as_of=date(2026, 6, 15))
        self.assertEqual(len(kochi_out.rows), 1)
        self.assertEqual(kochi_out.total_outstanding, Decimal("92000.00"))
        self.assertEqual(kochi_out.overdue_outstanding, Decimal("10000.00"))

        # Admin sees both Kochi (92,000) and Calicut (107,000) = 199,000 total outstanding
        admin_out = build_outstanding_report(self.admin_user, as_of=date(2026, 6, 15))
        self.assertEqual(len(admin_out.rows), 2)
        self.assertEqual(admin_out.total_outstanding, Decimal("199000.00"))
        self.assertEqual(admin_out.overdue_outstanding, Decimal("35000.00"))

        # Overdue installments report as of 2026-06-15 has 1 installment for Kochi
        as_of_date, overdue_rows, total_overdue = build_overdue_installments_report(
            self.accounts_kochi, as_of=date(2026, 6, 15)
        )
        self.assertEqual(as_of_date, date(2026, 6, 15))
        self.assertEqual(len(overdue_rows), 1)
        self.assertEqual(total_overdue, Decimal("10000.00"))
        self.assertEqual(overdue_rows[0].days_overdue, 45)

    def test_collection_report_separates_collection_centre_and_admission_centre(self):
        # Record a cross-centre payment: Kochi admission collected at Calicut by global admin
        record_payment(
            self.admission_kochi,
            amount=Decimal("5000.00"),
            paid_on=date(2026, 5, 15),
            mode=PaymentMode.CASH,
            collection_centre=self.centre_calicut,
            actor=self.admin_user,
            allocations=[{"installment": self.inst_kochi_c1, "amount": Decimal("5000.00")}],
        )

        report = build_collection_report(self.admin_user)
        self.assertEqual(report.posted_total, Decimal("42000.00"))

        by_collection = {row.code: row.posted_amount for row in report.by_collection_centre}
        by_admission = {row.code: row.posted_amount for row in report.by_admission_centre}

        # Collection centre totals: KC collected 26,000; CL collected 11,000 + 5,000 = 16,000
        self.assertEqual(by_collection["KC"], Decimal("26000.00"))
        self.assertEqual(by_collection["CL"], Decimal("16000.00"))

        # Admission centre totals: KC admission received 31,000; CL received 11,000
        self.assertEqual(by_admission["KC"], Decimal("31000.00"))
        self.assertEqual(by_admission["CL"], Decimal("11000.00"))

    def test_adjustments_report_tracks_refunds_reversals_and_reassignments(self):
        # Create an overpayment to generate advance credit (allocate nothing -> 5,000 credit lot)
        overpay = record_payment(
            self.admission_kochi,
            amount=Decimal("5000.00"),
            paid_on=date(2026, 5, 20),
            mode=PaymentMode.BANK_TRANSFER,
            collection_centre=self.centre_kochi,
            actor=self.accounts_kochi,
            allocations=[],
        )
        credit_lot = AdvanceCredit.objects.get(source_payment=overpay)
        refund = request_refund(
            credit_lot,
            amount=Decimal("3000.00"),
            reason="Excess payment refund requested by student",
            mode=PaymentMode.BANK_TRANSFER,
            actor=self.accounts_kochi,
            request_key="refund-kochi-001",
        )
        decide_refund(
            refund,
            approve=True,
            actor=self.admin_user,
            note="Approved bank refund",
            refunded_on=date(2026, 5, 22),
        )

        adj = build_adjustments_report(self.accounts_kochi)
        self.assertEqual(len(adj["refunds"]), 1)
        self.assertEqual(adj["refunds"][0].amount, Decimal("3000.00"))
        self.assertEqual(len(adj["reversals"]), 0)

    def test_report_views_csv_and_pdf_exports_enforce_access_and_sanitize_formulas(self):
        # ADMISSIONS can view report hub and admission register HTML, 403 on finance/exports
        self.client.force_login(self.admissions_kochi)
        self.assertEqual(self.client.get(reverse("reports:report_hub")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports:ledger_index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports:admission_register")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("reports:admission_register_csv")).status_code, 403
        )
        self.assertEqual(self.client.get(reverse("reports:outstanding_report")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:collection_report")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:audit_trail")).status_code, 403)

        # ACCOUNTS user can access all reports + CSV/PDF exports for their centre scope
        self.client.force_login(self.accounts_kochi)
        for url_name in [
            "reports:report_hub",
            "reports:ledger_index",
            "reports:admission_register",
            "reports:outstanding_report",
            "reports:overdue_report",
            "reports:collection_report",
            "reports:adjustments_report",
            "reports:audit_trail",
        ]:
            res = self.client.get(reverse(url_name))
            self.assertEqual(res.status_code, 200, f"Expected 200 for {url_name}")

        # Verify CSV exports sanitize formula characters ('=UPI-REF-001')
        col_csv = self.client.get(reverse("reports:collection_report_csv"))
        self.assertEqual(col_csv.status_code, 200)
        csv_text = col_csv.content.decode("utf-8")
        self.assertIn("'=UPI-REF-001", csv_text)
        self.assertNotIn(
            "Ananya Menon", csv_text
        )  # Calicut student excluded for Kochi accounts user

        # Verify PDF exports return valid %PDF-1.4 documents
        for pdf_url_name in [
            "reports:admission_register_pdf",
            "reports:outstanding_report_pdf",
            "reports:overdue_report_pdf",
            "reports:collection_report_pdf",
            "reports:adjustments_report_pdf",
            "reports:audit_trail_pdf",
        ]:
            pdf_res = self.client.get(reverse(pdf_url_name))
            self.assertEqual(pdf_res.status_code, 200, f"Expected 200 for {pdf_url_name}")
            self.assertEqual(pdf_res["Content-Type"], "application/pdf")
            self.assertTrue(pdf_res.content.startswith(b"%PDF-1.4\n"))

    def test_dashboard_view_renders_live_scoped_metrics(self):
        summary_kochi = dashboard_summary(self.accounts_kochi)
        self.assertEqual(summary_kochi["total_admissions"], 1)
        self.assertEqual(summary_kochi["total_outstanding"], Decimal("92000.00"))

        self.client.force_login(self.accounts_kochi)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "92000.00")
        self.assertNotContains(res, "Presentation-Only Demo Notice")

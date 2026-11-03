"""Receipt numbering: format, scoping, financial-year boundaries, concurrency."""

import threading
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.academics.models import AcademicYear, Batch, Course
from apps.admissions.models import Admission
from apps.centres.models import Centre
from apps.fees.services import approve_fee_plan, create_draft_fee_plan
from apps.fees.tests.factories import (
    COURSE_FEE,
    GST_RATE,
    default_components,
    make_centre,
    make_student,
    make_user,
)
from apps.payments.models import Payment, PaymentReversal, Receipt, ReceiptSequence
from apps.payments.services import (
    build_receipt_number,
    decide_payment_reversal,
    financial_year_for,
    record_payment,
    request_payment_reversal,
)
from apps.payments.tests.factories import PaymentTestBase

REVERSAL_REASON = "Receipt was issued against a collection that must be reversed."


class ReceiptNumberingTests(PaymentTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self.make_approved_plan()
        self.admission = self.plan.admission

    def record(self, *, amount=Decimal("1000.00"), paid_on=date(2026, 7, 15), **kwargs):
        kwargs.setdefault("admission", self.admission)
        kwargs.setdefault("collection_centre", self.centre)
        kwargs.setdefault("actor", self.accounts)
        return self.make_payment(amount=amount, paid_on=paid_on, **kwargs)

    def test_receipt_number_format(self):
        payment = self.record()

        receipt = payment.receipt
        self.assertEqual(receipt.number, "GIIMS/TVM/FY26-27/000001")
        self.assertEqual(receipt.centre, self.centre)
        self.assertEqual(receipt.financial_year, "FY26-27")
        self.assertEqual(receipt.sequence, 1)
        self.assertEqual(receipt.issued_by, self.accounts)
        self.assertEqual(receipt.status, Receipt.Status.ACTIVE)

    def test_sequence_is_padded_to_six_digits(self):
        self.assertEqual(build_receipt_number("KOC", "FY26-27", 42), "GIIMS/KOC/FY26-27/000042")
        self.assertEqual(build_receipt_number("KOC", "FY25-26", 123456), "GIIMS/KOC/FY25-26/123456")

        for _ in range(11):
            payment = self.record()

        self.assertEqual(payment.receipt.number, "GIIMS/TVM/FY26-27/000011")

    def test_sequences_are_monotonic_and_never_reused(self):
        numbers = [self.record().receipt.number for _ in range(3)]

        self.assertEqual(
            numbers,
            [
                "GIIMS/TVM/FY26-27/000001",
                "GIIMS/TVM/FY26-27/000002",
                "GIIMS/TVM/FY26-27/000003",
            ],
        )
        counter = ReceiptSequence.objects.get(centre=self.centre, financial_year="FY26-27")
        self.assertEqual(counter.last_sequence, 3)

    def test_sequence_is_monotonic_even_when_payment_dates_are_out_of_order(self):
        later = self.record(paid_on=date(2027, 1, 10))
        earlier = self.record(paid_on=date(2026, 5, 1))

        self.assertEqual(later.receipt.sequence, 1)
        self.assertEqual(earlier.receipt.sequence, 2)

    def test_centre_sequences_are_independent(self):
        other_admission = self.make_admission(centre=self.other_centre)
        self.make_approved_plan(admission=other_admission)

        tvm_payment = self.record()
        koc_payment = self.make_payment(
            admission=other_admission,
            amount=Decimal("1000.00"),
            collection_centre=self.other_centre,
            actor=self.admin,
        )

        self.assertEqual(tvm_payment.receipt.number, "GIIMS/TVM/FY26-27/000001")
        self.assertEqual(koc_payment.receipt.number, "GIIMS/KOC/FY26-27/000001")
        self.assertNotEqual(tvm_payment.receipt.pk, koc_payment.receipt.pk)

    def test_collection_centre_sequence_is_used_even_when_admission_is_elsewhere(self):
        self.accounts.centres.add(self.other_centre)

        payment = self.make_payment(
            admission=self.admission,
            amount=Decimal("1000.00"),
            collection_centre=self.other_centre,
        )

        self.assertEqual(payment.centre, self.other_centre)
        self.assertEqual(payment.admission.centre, self.centre)
        self.assertEqual(payment.receipt.centre, self.other_centre)
        self.assertEqual(payment.receipt.number, "GIIMS/KOC/FY26-27/000001")

    def test_financial_year_boundary_uses_paid_on(self):
        self.assertEqual(financial_year_for(date(2026, 3, 31)), "FY25-26")
        self.assertEqual(financial_year_for(date(2026, 4, 1)), "FY26-27")

        march = self.record(amount=Decimal("1000.00"), paid_on=date(2026, 3, 31))
        april = self.record(amount=Decimal("1000.00"), paid_on=date(2026, 4, 1))

        self.assertEqual(march.receipt.financial_year, "FY25-26")
        self.assertEqual(march.receipt.number, "GIIMS/TVM/FY25-26/000001")
        self.assertEqual(april.receipt.financial_year, "FY26-27")
        self.assertEqual(april.receipt.number, "GIIMS/TVM/FY26-27/000001")

    def test_reversed_receipt_keeps_its_number_and_record(self):
        payment = self.record()
        original_number = payment.receipt.number

        reversal = request_payment_reversal(
            payment, reason=REVERSAL_REASON, actor=self.accounts, request_key="rev-1"
        )
        decide_payment_reversal(reversal, approve=True, actor=self.admin)

        receipt = Receipt.objects.get(payment=payment)
        self.assertEqual(receipt.number, original_number)
        self.assertEqual(receipt.status, Receipt.Status.REVERSED)
        self.assertEqual(Receipt.objects.filter(pk=receipt.pk).count(), 1)

        next_payment = self.record()
        self.assertEqual(next_payment.receipt.number, "GIIMS/TVM/FY26-27/000002")

    def test_database_rejects_a_duplicate_receipt_number(self):
        first = self.record()
        duplicate_payment = Payment.objects.create(
            centre=self.centre,
            admission=self.admission,
            amount=Decimal("10.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            recorded_by=self.accounts,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Receipt.objects.create(
                payment=duplicate_payment,
                centre=self.centre,
                financial_year="FY26-27",
                sequence=99,
                number=first.receipt.number,
                issued_by=self.accounts,
            )

    def test_database_rejects_duplicate_centre_year_sequence(self):
        first = self.record()
        duplicate_payment = Payment.objects.create(
            centre=self.centre,
            admission=self.admission,
            amount=Decimal("10.00"),
            paid_on=date(2026, 7, 15),
            mode="CASH",
            recorded_by=self.accounts,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Receipt.objects.create(
                payment=duplicate_payment,
                centre=self.centre,
                financial_year=first.receipt.financial_year,
                sequence=first.receipt.sequence,
                number="GIIMS/TVM/FY26-27/999999",
                issued_by=self.accounts,
            )


class ReceiptConcurrencyTests(TransactionTestCase):
    """
    Two real threads collecting at the same centre and financial year.

    PostgreSQL row locking is exercised because the test database commits
    between the setup and the threads. Skipped only if the platform cannot run
    threads against the database (see the module report).
    """

    def setUp(self):
        today = timezone.localdate()
        self.centre = make_centre()
        self.academic_year = AcademicYear.objects.create(
            label="2026-2027",
            start_date=today,
            end_date=today.replace(year=today.year + 1),
            is_active=True,
        )
        self.course = Course.objects.create(
            code="LSC-CONC", name="Logistics & SCM", duration_months=12, is_active=True
        )
        self.batch = Batch.objects.create(
            centre=self.centre,
            course=self.course,
            academic_year=self.academic_year,
            code="JUL26C",
            start_date=today,
            end_date=today.replace(year=today.year + 1),
            is_active=True,
        )
        self.admin = make_user("concurrent_admin", role="ADMIN")
        self.accounts = make_user("concurrent_accounts", role="ACCOUNTS")
        self.accounts.centres.add(self.centre)
        self.admissions = []
        for index in range(2):
            admission = Admission.objects.create(
                student=make_student(first_name=f"Concurrent{index}"),
                centre=self.centre,
                course=self.course,
                batch=self.batch,
                academic_year=self.academic_year,
                status=Admission.Status.ENROLLED,
            )
            draft = create_draft_fee_plan(
                admission,
                course_fee=COURSE_FEE,
                gst_rate=GST_RATE,
                components=default_components(),
                actor=self.accounts,
            )
            approve_fee_plan(draft, self.admin)
            self.admissions.append(admission)

    def test_two_concurrent_collections_receive_distinct_numbers(self):
        if connection.vendor == "sqlite":
            self.skipTest("SQLite does not support concurrent row-locked writes across threads.")
        barrier = threading.Barrier(2)
        numbers: list[str] = []
        errors: list[BaseException] = []

        def collect(admission):
            try:
                barrier.wait(timeout=15)
                payment = record_payment(
                    admission,
                    amount=Decimal("1000.00"),
                    paid_on=date(2026, 7, 15),
                    mode="CASH",
                    collection_centre=Centre.objects.get(pk=self.centre.pk),
                    actor=self.accounts,
                )
                numbers.append(payment.receipt.number)
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=collect, args=(admission,), daemon=True)
            for admission in self.admissions
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted(numbers),
            ["GIIMS/TVM/FY26-27/000001", "GIIMS/TVM/FY26-27/000002"],
        )
        self.assertEqual(
            ReceiptSequence.objects.get(centre=self.centre, financial_year="FY26-27").last_sequence,
            2,
        )
        self.assertEqual(Receipt.objects.filter(centre=self.centre).count(), 2)
        self.assertEqual(
            PaymentReversal.objects.count(), 0, "Sanity: no reversal rows in this test."
        )

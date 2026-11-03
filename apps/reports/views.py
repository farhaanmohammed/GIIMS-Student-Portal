"""
Views and CSV/PDF export endpoints for Student Fee Ledgers, Operational &
Financial Reports, and the Financial Audit Trail.

Authorization & centre scoping:
- ``ADMIN``, ``MANAGEMENT``, and ``ACCOUNTS`` have access to all financial
  reports, audit trails, and CSV/PDF exports within their accessible centres.
- ``ADMISSIONS`` has read-only access to the Reports Hub, the Admission &
  Student Register, and Student Fee Ledgers within their accessible centres,
  and receives 403 on financial reports and exports.
- Cross-centre admission IDs resolve to 404; cross-centre filter parameters are
  ignored.
"""

from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from apps.academics.models import AcademicYear, Batch, Course
from apps.admissions.models import Admission
from apps.core.exporters import (
    PdfTableSection,
    build_csv_response,
    build_pdf_document,
    build_pdf_response,
)
from apps.core.permissions import role_required
from apps.fees.models import InstallmentDueDateRevision
from apps.payments.models import FinancialAuditEvent, Payment, PaymentMode
from apps.reports.selectors import (
    admissions_register_queryset,
    audit_events_queryset,
    build_adjustments_report,
    build_collection_report,
    build_outstanding_report,
    build_overdue_installments_report,
    build_student_ledger,
    parse_report_date,
)

User = get_user_model()

PAGE_SIZE = 25
LEDGER_AND_REGISTER_ROLES = ("ADMIN", "MANAGEMENT", "ACCOUNTS", "ADMISSIONS")
FINANCIAL_REPORT_ROLES = ("ADMIN", "MANAGEMENT", "ACCOUNTS")


def _can_access_financial_reports(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in FINANCIAL_REPORT_ROLES)
    )


def _urlencode_filters(filters: dict[str, Any]) -> str:
    return urlencode({key: value for key, value in filters.items() if value not in ("", None)})


def _parse_optional_date(raw: str | None) -> date | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _accessible_centre_id(user, raw: str | None) -> int | None:
    if not raw or not str(raw).isdigit():
        return None
    cid = int(raw)
    allowed = set(user.get_accessible_centres().values_list("pk", flat=True))
    return cid if cid in allowed else None


def _valid_pk(queryset, raw: str | None) -> int | None:
    if not raw or not str(raw).isdigit():
        return None
    pk = int(raw)
    return pk if queryset.filter(pk=pk).exists() else None


def _paginate(request: HttpRequest, items, per_page: int = PAGE_SIZE):
    paginator = Paginator(items, per_page)
    raw_page = request.GET.get("page", "1")
    page_num = int(raw_page) if str(raw_page).isdigit() and int(raw_page) > 0 else 1
    page_obj = paginator.get_page(page_num)
    return page_obj, paginator.get_elided_page_range(page_obj.number), paginator.count


class RoleRequiredViewMixin:
    """Role gate placed after ``LoginRequiredMixin``."""

    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        checker = role_required(*self.allowed_roles)
        return checker(super().dispatch)(request, *args, **kwargs)


class ReportsHubView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """Landing page listing all reports available to the user's role."""

    template_name = "reports/report_hub.html"
    allowed_roles = LEDGER_AND_REGISTER_ROLES

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {
                "can_access_financial_reports": _can_access_financial_reports(request.user),
                "today": timezone.localdate(),
            },
        )


# ---------------------------------------------------------------------------
# 1. Student Fee Ledger (picker + detail + CSV + PDF)
# ---------------------------------------------------------------------------
class StudentLedgerIndexView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """Search/filter enrolled or enquired admissions to open their fee ledger."""

    template_name = "reports/ledger_index.html"
    allowed_roles = LEDGER_AND_REGISTER_ROLES

    def get(self, request: HttpRequest) -> HttpResponse:
        accessible_centres = request.user.get_accessible_centres()
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        query = (request.GET.get("q") or "").strip()[:100]
        status = request.GET.get("status", "")
        if status not in dict(Admission.Status.choices):
            status = ""

        qs = admissions_register_queryset(
            request.user,
            centre_id=centre_id,
            status=status,
            query=query,
        )
        page_obj, page_range, total_count = _paginate(request, qs)
        filters = {
            "q": query,
            "centre": str(centre_id) if centre_id else "",
            "status": status,
        }
        return render(
            request,
            self.template_name,
            {
                "admissions": page_obj.object_list,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": filters,
                "querystring": _urlencode_filters(filters),
                "centre_options": accessible_centres,
                "status_options": Admission.Status.choices,
                "can_export": _can_access_financial_reports(request.user),
            },
        )


class StudentLedgerDetailView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """Comprehensive fee ledger for a single admission as of a chosen date."""

    template_name = "reports/student_ledger.html"
    allowed_roles = LEDGER_AND_REGISTER_ROLES

    def get(self, request: HttpRequest, admission_id: int) -> HttpResponse:
        admission = get_object_or_404(
            Admission.objects.for_user(request.user).select_related(
                "student", "centre", "course", "batch", "academic_year"
            ),
            pk=admission_id,
        )
        as_of = parse_report_date(request.GET.get("as_of"))
        ledger = build_student_ledger(admission, as_of=as_of)
        return render(
            request,
            self.template_name,
            {
                "admission": admission,
                "ledger": ledger,
                "as_of": as_of,
                "can_export": _can_access_financial_reports(request.user),
            },
        )


class StudentLedgerCsvView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """CSV export of a student's fee ledger."""

    allowed_roles = FINANCIAL_REPORT_ROLES

    def get(self, request: HttpRequest, admission_id: int) -> HttpResponse:
        admission = get_object_or_404(
            Admission.objects.for_user(request.user).select_related(
                "student", "centre", "course", "batch", "academic_year"
            ),
            pk=admission_id,
        )
        as_of = parse_report_date(request.GET.get("as_of"))
        ledger = build_student_ledger(admission, as_of=as_of)
        student = admission.student

        metadata_rows = [
            ["Report", "Student Fee Ledger"],
            ["Generated At", timezone.localtime()],
            ["As Of Date", as_of],
            ["Student ID", student.student_id or "Not enrolled"],
            ["Student Name", student.get_full_name()],
            ["Centre", f"{admission.centre.name} ({admission.centre.code})"],
            ["Course / Batch", f"{admission.course.code} / {admission.batch.code}"],
            ["Total Billed (INR)", ledger.summary.total_billed],
            ["Total Paid (INR)", ledger.summary.total_paid],
            ["Total Outstanding (INR)", ledger.summary.total_outstanding],
            ["Overdue Outstanding (INR)", ledger.summary.overdue_outstanding],
            ["Not-Yet-Due Outstanding (INR)", ledger.summary.not_yet_due_outstanding],
            ["Stage-Based Undated Outstanding (INR)", ledger.summary.undated_outstanding],
            ["Available Advance Credit (INR)", ledger.summary.available_credit],
        ]
        headers = [
            "Section",
            "Sequence / Reference",
            "Description / Mode",
            "Category / Centre",
            "Original Date / Paid On",
            "Effective Date",
            "Billed / Amount (INR)",
            "Paid / Allocated (INR)",
            "Outstanding / Remaining (INR)",
            "Status",
        ]
        rows: list[list[Any]] = []
        for inst_row in ledger.installment_rows:
            status_label = (
                f"Overdue ({inst_row.days_overdue}d)"
                if inst_row.is_overdue
                else ("Paid" if inst_row.outstanding == 0 else "Scheduled")
            )
            rows.append(
                [
                    "Installment Schedule",
                    f"#{inst_row.installment.sequence}",
                    inst_row.installment.description,
                    inst_row.installment.get_category_display(),
                    inst_row.original_due_date,
                    inst_row.effective_due_date,
                    inst_row.billed,
                    inst_row.allocated,
                    inst_row.outstanding,
                    status_label,
                ]
            )
        for payment in ledger.payments:
            receipt_no = payment.receipt.number if hasattr(payment, "receipt") else f"#{payment.pk}"
            rows.append(
                [
                    "Payment",
                    receipt_no,
                    payment.get_mode_display(),
                    f"Collected at {payment.centre.code}",
                    payment.paid_on,
                    "",
                    payment.amount,
                    "",
                    "",
                    payment.get_status_display(),
                ]
            )
        for lot in ledger.credit_lots:
            rows.append(
                [
                    "Advance Credit Lot",
                    f"Lot #{lot.pk}",
                    lot.get_source_type_display(),
                    admission.centre.code,
                    lot.created_at.date(),
                    "",
                    lot.original_amount,
                    lot.original_amount - lot.remaining_amount,
                    lot.remaining_amount,
                    lot.get_status_display(),
                ]
            )

        ident = student.student_id or f"admission_{admission.pk}"
        return build_csv_response(
            f"student_ledger_{ident}_{as_of.isoformat()}.csv",
            headers,
            rows,
            metadata_rows=metadata_rows,
        )


class StudentLedgerPdfView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """Server-generated PDF download of a student's fee ledger."""

    allowed_roles = FINANCIAL_REPORT_ROLES

    def get(self, request: HttpRequest, admission_id: int) -> HttpResponse:
        admission = get_object_or_404(
            Admission.objects.for_user(request.user).select_related(
                "student", "centre", "course", "batch", "academic_year"
            ),
            pk=admission_id,
        )
        as_of = parse_report_date(request.GET.get("as_of"))
        ledger = build_student_ledger(admission, as_of=as_of)
        student = admission.student

        metadata = [
            ("Student Name", student.get_full_name()),
            ("Student ID", student.student_id or "Not enrolled"),
            ("Admission Centre", f"{admission.centre.name} ({admission.centre.code})"),
            ("Course / Batch", f"{admission.course.code} / {admission.batch.code}"),
            ("Academic Year", admission.academic_year.label),
            ("As-Of Date", as_of),
            ("Total Billed (INR)", ledger.summary.total_billed),
            ("Total Paid (INR)", ledger.summary.total_paid),
            ("Total Outstanding (INR)", ledger.summary.total_outstanding),
            ("Overdue Balance (INR)", ledger.summary.overdue_outstanding),
            ("Not-Yet-Due (INR)", ledger.summary.not_yet_due_outstanding),
            ("Available Credit (INR)", ledger.summary.available_credit),
        ]

        inst_rows = [
            (
                f"#{r.installment.sequence} {r.installment.description}",
                r.installment.get_category_display(),
                r.original_due_date,
                r.effective_due_date,
                r.billed,
                r.allocated,
                r.outstanding,
                f"Overdue ({r.days_overdue}d)"
                if r.is_overdue
                else ("Paid" if r.outstanding == 0 else "Open"),
            )
            for r in ledger.installment_rows
        ]
        pay_rows = [
            (
                p.receipt.number if hasattr(p, "receipt") else f"#{p.pk}",
                p.paid_on,
                p.centre.code,
                p.get_mode_display(),
                p.reference or "-",
                p.amount,
                p.get_status_display(),
            )
            for p in ledger.payments
        ]
        sections = [
            PdfTableSection(
                title=f"Installment Schedule & Balances (As of {as_of.strftime('%d %b %Y')})",
                headers=(
                    "Installment",
                    "Stage",
                    "Orig Due",
                    "Eff Due",
                    "Billed",
                    "Paid",
                    "Balance",
                    "Status",
                ),
                rows=inst_rows,
                col_widths=(24, 11, 11, 11, 11, 11, 11, 10),
                empty_message="No approved fee plan assigned.",
            ),
            PdfTableSection(
                title="Payment & Receipt History",
                headers=(
                    "Receipt #",
                    "Paid On",
                    "Coll. Centre",
                    "Mode",
                    "Reference",
                    "Amount (INR)",
                    "Status",
                ),
                rows=pay_rows,
                col_widths=(24, 13, 12, 14, 15, 12, 10),
                empty_message="No payments recorded for this admission.",
            ),
        ]
        pdf_bytes = build_pdf_document(
            title=f"Student Fee Ledger - {student.get_full_name()}",
            subtitle=(
                f"Student ID: {student.student_id or 'Unassigned'} | "
                f"As of {as_of.strftime('%d %b %Y')}"
            ),
            status_badge=admission.get_status_display(),
            metadata=metadata,
            sections=sections,
            landscape=True,
        )
        ident = student.student_id or f"admission_{admission.pk}"
        return build_pdf_response(f"student_ledger_{ident}_{as_of.isoformat()}.pdf", pdf_bytes)


# ---------------------------------------------------------------------------
# 2. Student & Admission Register (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class AdmissionRegisterReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    """Filterable Student & Admission Register."""

    template_name = "reports/admission_register.html"
    allowed_roles = LEDGER_AND_REGISTER_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        course_id = _valid_pk(Course.objects.all(), request.GET.get("course"))
        batch_id = _valid_pk(
            Batch.objects.for_user(request.user),
            request.GET.get("batch"),
        )
        year_id = _valid_pk(AcademicYear.objects.all(), request.GET.get("academic_year"))
        status = request.GET.get("status", "")
        if status not in dict(Admission.Status.choices):
            status = ""
        date_from = _parse_optional_date(request.GET.get("date_from"))
        date_to = _parse_optional_date(request.GET.get("date_to"))
        query = (request.GET.get("q") or "").strip()[:100]
        return {
            "centre_id": centre_id,
            "course_id": course_id,
            "batch_id": batch_id,
            "academic_year_id": year_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "query": query,
            "raw": {
                "centre": str(centre_id) if centre_id else "",
                "course": str(course_id) if course_id else "",
                "batch": str(batch_id) if batch_id else "",
                "academic_year": str(year_id) if year_id else "",
                "status": status,
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
                "q": query,
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = admissions_register_queryset(
            request.user,
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            academic_year_id=f["academic_year_id"],
            status=f["status"],
            date_from=f["date_from"],
            date_to=f["date_to"],
            query=f["query"],
        )
        page_obj, page_range, total_count = _paginate(request, qs)
        return render(
            request,
            self.template_name,
            {
                "admissions": page_obj.object_list,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
                "course_options": Course.objects.order_by("code"),
                "batch_options": Batch.objects.for_user(request.user)
                .select_related("centre", "course")
                .order_by("centre__code", "code"),
                "year_options": AcademicYear.objects.order_by("-start_date"),
                "status_options": Admission.Status.choices,
                "can_export": _can_access_financial_reports(request.user),
            },
        )


class AdmissionRegisterCsvView(AdmissionRegisterReportView):
    allowed_roles = FINANCIAL_REPORT_ROLES

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = admissions_register_queryset(
            request.user,
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            academic_year_id=f["academic_year_id"],
            status=f["status"],
            date_from=f["date_from"],
            date_to=f["date_to"],
            query=f["query"],
        )
        headers = [
            "Student ID",
            "Student Name",
            "Phone",
            "Email",
            "Centre",
            "Academic Year",
            "Course",
            "Batch",
            "Status",
            "Enquired At",
            "Enrolled At",
        ]
        rows = [
            [
                adm.student.student_id or "",
                adm.student.get_full_name(),
                adm.student.phone,
                adm.student.email,
                f"{adm.centre.code} - {adm.centre.name}",
                adm.academic_year.label,
                f"{adm.course.code} - {adm.course.name}",
                adm.batch.code,
                adm.get_status_display(),
                adm.enquired_at,
                adm.enrolled_at,
            ]
            for adm in qs
        ]
        return build_csv_response(
            f"admission_register_{timezone.localdate().isoformat()}.csv",
            headers,
            rows,
            metadata_rows=[
                ["Report", "Student & Admission Register"],
                ["Generated At", timezone.localtime()],
            ],
        )


class AdmissionRegisterPdfView(AdmissionRegisterReportView):
    allowed_roles = FINANCIAL_REPORT_ROLES

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = list(
            admissions_register_queryset(
                request.user,
                centre_id=f["centre_id"],
                course_id=f["course_id"],
                batch_id=f["batch_id"],
                academic_year_id=f["academic_year_id"],
                status=f["status"],
                date_from=f["date_from"],
                date_to=f["date_to"],
                query=f["query"],
            )
        )
        rows = [
            (
                adm.student.student_id or "-",
                adm.student.get_full_name(),
                adm.student.phone,
                adm.centre.code,
                adm.course.code,
                adm.batch.code,
                adm.academic_year.label,
                adm.get_status_display(),
                adm.enquired_at.date() if adm.enquired_at else None,
            )
            for adm in qs
        ]
        today_str = timezone.localdate().strftime("%d %b %Y")
        pdf_bytes = build_pdf_document(
            title="Student & Admission Register",
            subtitle=f"Total Records: {len(rows)} | Generated on {today_str}",
            sections=[
                PdfTableSection(
                    title="Admissions",
                    headers=(
                        "Student ID",
                        "Student Name",
                        "Phone",
                        "Centre",
                        "Course",
                        "Batch",
                        "Year",
                        "Status",
                        "Enquired",
                    ),
                    rows=rows,
                    col_widths=(15, 20, 12, 8, 10, 10, 9, 8, 8),
                )
            ],
            landscape=True,
        )
        return build_pdf_response(
            f"admission_register_{timezone.localdate().isoformat()}.pdf", pdf_bytes
        )


# ---------------------------------------------------------------------------
# 3. Outstanding Report by Student/Admission (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class OutstandingReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "reports/outstanding_report.html"
    allowed_roles = FINANCIAL_REPORT_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        as_of = parse_report_date(request.GET.get("as_of"))
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        course_id = _valid_pk(Course.objects.all(), request.GET.get("course"))
        batch_id = _valid_pk(Batch.objects.for_user(request.user), request.GET.get("batch"))
        year_id = _valid_pk(AcademicYear.objects.all(), request.GET.get("academic_year"))
        only_outstanding = request.GET.get("only_outstanding") == "1"
        query = (request.GET.get("q") or "").strip()[:100]
        return {
            "as_of": as_of,
            "centre_id": centre_id,
            "course_id": course_id,
            "batch_id": batch_id,
            "academic_year_id": year_id,
            "only_outstanding": only_outstanding,
            "query": query,
            "raw": {
                "as_of": as_of.isoformat(),
                "centre": str(centre_id) if centre_id else "",
                "course": str(course_id) if course_id else "",
                "batch": str(batch_id) if batch_id else "",
                "academic_year": str(year_id) if year_id else "",
                "only_outstanding": "1" if only_outstanding else "",
                "q": query,
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_outstanding_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            academic_year_id=f["academic_year_id"],
            only_outstanding=f["only_outstanding"],
            query=f["query"],
        )
        page_obj, page_range, total_count = _paginate(request, report.rows)
        return render(
            request,
            self.template_name,
            {
                "report": report,
                "rows": page_obj.object_list,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
                "course_options": Course.objects.order_by("code"),
                "batch_options": Batch.objects.for_user(request.user)
                .select_related("centre", "course")
                .order_by("centre__code", "code"),
                "year_options": AcademicYear.objects.order_by("-start_date"),
            },
        )


class OutstandingReportCsvView(OutstandingReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_outstanding_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            academic_year_id=f["academic_year_id"],
            only_outstanding=f["only_outstanding"],
            query=f["query"],
        )
        headers = [
            "Student ID",
            "Student Name",
            "Centre",
            "Course",
            "Batch",
            "Total Billed (INR)",
            "Total Paid (INR)",
            "Total Outstanding (INR)",
            "Overdue Outstanding (INR)",
            "Not-Yet-Due Outstanding (INR)",
            "Stage-Based Undated Outstanding (INR)",
            "Available Advance Credit (INR)",
        ]
        rows = [
            [
                r.admission.student.student_id or "",
                r.admission.student.get_full_name(),
                r.admission.centre.code,
                r.admission.course.code,
                r.admission.batch.code,
                r.total_billed,
                r.total_paid,
                r.total_outstanding,
                r.overdue_outstanding,
                r.not_yet_due_outstanding,
                r.undated_outstanding,
                r.available_credit,
            ]
            for r in report.rows
        ]
        rows.append(
            [
                "TOTAL",
                f"{len(report.rows)} admission(s)",
                "",
                "",
                "",
                report.total_billed,
                report.total_paid,
                report.total_outstanding,
                report.overdue_outstanding,
                report.not_yet_due_outstanding,
                report.undated_outstanding,
                report.available_credit,
            ]
        )
        return build_csv_response(
            f"outstanding_report_{report.as_of.isoformat()}.csv",
            headers,
            rows,
            metadata_rows=[
                ["Report", "Student & Aggregate Outstanding Report"],
                ["As Of Date", report.as_of],
                ["Generated At", timezone.localtime()],
            ],
        )


class OutstandingReportPdfView(OutstandingReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_outstanding_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            academic_year_id=f["academic_year_id"],
            only_outstanding=f["only_outstanding"],
            query=f["query"],
        )
        rows = [
            (
                r.admission.student.student_id or "-",
                r.admission.student.get_full_name(),
                r.admission.centre.code,
                f"{r.admission.course.code}/{r.admission.batch.code}",
                r.total_billed,
                r.total_paid,
                r.total_outstanding,
                r.overdue_outstanding,
                r.not_yet_due_outstanding,
                r.undated_outstanding,
                r.available_credit,
            )
            for r in report.rows
        ]
        summary_rows = [
            (
                "TOTAL",
                f"{len(report.rows)} admission(s)",
                "-",
                "-",
                report.total_billed,
                report.total_paid,
                report.total_outstanding,
                report.overdue_outstanding,
                report.not_yet_due_outstanding,
                report.undated_outstanding,
                report.available_credit,
            )
        ]
        pdf_bytes = build_pdf_document(
            title="Student-Wise & Aggregate Outstanding Report",
            subtitle=f"As of {report.as_of.strftime('%d %b %Y')} | {len(report.rows)} Admission(s)",
            metadata=[
                ("As-Of Date", report.as_of),
                ("Total Billed (INR)", report.total_billed),
                ("Total Paid (INR)", report.total_paid),
                ("Total Outstanding (INR)", report.total_outstanding),
                ("Overdue Outstanding (INR)", report.overdue_outstanding),
                ("Available Credit (INR)", report.available_credit),
            ],
            sections=[
                PdfTableSection(
                    title="Admission Balances",
                    headers=(
                        "Student ID",
                        "Student Name",
                        "Centre",
                        "Course/Batch",
                        "Billed",
                        "Paid",
                        "Outstanding",
                        "Overdue",
                        "Not Yet Due",
                        "Undated",
                        "Credit",
                    ),
                    rows=rows,
                    col_widths=(12, 16, 6, 11, 8, 8, 9, 8, 8, 7, 7),
                    summary_rows=summary_rows,
                )
            ],
            landscape=True,
        )
        return build_pdf_response(f"outstanding_report_{report.as_of.isoformat()}.pdf", pdf_bytes)


# ---------------------------------------------------------------------------
# 4. Overdue Installments Report (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class OverdueInstallmentsReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "reports/overdue_report.html"
    allowed_roles = FINANCIAL_REPORT_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        as_of = parse_report_date(request.GET.get("as_of"))
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        course_id = _valid_pk(Course.objects.all(), request.GET.get("course"))
        batch_id = _valid_pk(Batch.objects.for_user(request.user), request.GET.get("batch"))
        query = (request.GET.get("q") or "").strip()[:100]
        return {
            "as_of": as_of,
            "centre_id": centre_id,
            "course_id": course_id,
            "batch_id": batch_id,
            "query": query,
            "raw": {
                "as_of": as_of.isoformat(),
                "centre": str(centre_id) if centre_id else "",
                "course": str(course_id) if course_id else "",
                "batch": str(batch_id) if batch_id else "",
                "q": query,
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        as_of, rows, total_overdue = build_overdue_installments_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            query=f["query"],
        )
        page_obj, page_range, total_count = _paginate(request, rows)
        return render(
            request,
            self.template_name,
            {
                "as_of": as_of,
                "rows": page_obj.object_list,
                "total_overdue": total_overdue,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
                "course_options": Course.objects.order_by("code"),
                "batch_options": Batch.objects.for_user(request.user)
                .select_related("centre", "course")
                .order_by("centre__code", "code"),
            },
        )


class OverdueInstallmentsCsvView(OverdueInstallmentsReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        as_of, rows, total_overdue = build_overdue_installments_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            query=f["query"],
        )
        headers = [
            "Student ID",
            "Student Name",
            "Centre",
            "Course",
            "Batch",
            "Installment #",
            "Installment Description",
            "Original Due Date",
            "Effective Due Date",
            "Days Overdue",
            "Billed (INR)",
            "Paid (INR)",
            "Overdue Balance (INR)",
        ]
        csv_rows = [
            [
                r.admission.student.student_id or "",
                r.admission.student.get_full_name(),
                r.admission.centre.code,
                r.admission.course.code,
                r.admission.batch.code,
                r.installment.sequence,
                r.installment.description,
                r.original_due_date,
                r.effective_due_date,
                r.days_overdue,
                r.billed,
                r.paid,
                r.overdue_amount,
            ]
            for r in rows
        ]
        csv_rows.append(
            [
                "TOTAL",
                f"{len(rows)} overdue installment(s)",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                total_overdue,
            ]
        )
        return build_csv_response(
            f"overdue_installments_{as_of.isoformat()}.csv",
            headers,
            csv_rows,
            metadata_rows=[
                ["Report", "Overdue Installments Report"],
                ["As Of Date", as_of],
                ["Total Overdue (INR)", total_overdue],
            ],
        )


class OverdueInstallmentsPdfView(OverdueInstallmentsReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        as_of, rows, total_overdue = build_overdue_installments_report(
            request.user,
            as_of=f["as_of"],
            centre_id=f["centre_id"],
            course_id=f["course_id"],
            batch_id=f["batch_id"],
            query=f["query"],
        )
        pdf_rows = [
            (
                r.admission.student.student_id or "-",
                r.admission.student.get_full_name(),
                r.admission.centre.code,
                f"#{r.installment.sequence} {r.installment.description}",
                r.original_due_date,
                r.effective_due_date,
                f"{r.days_overdue}d",
                r.billed,
                r.paid,
                r.overdue_amount,
            )
            for r in rows
        ]
        pdf_bytes = build_pdf_document(
            title="Overdue Installments Report",
            subtitle=f"As of {as_of.strftime('%d %b %Y')} | Total Overdue: INR {total_overdue:.2f}",
            sections=[
                PdfTableSection(
                    title="Overdue Course Installments",
                    headers=(
                        "Student ID",
                        "Student Name",
                        "Centre",
                        "Installment",
                        "Orig Due",
                        "Eff Due",
                        "Days",
                        "Billed",
                        "Paid",
                        "Overdue (INR)",
                    ),
                    rows=pdf_rows,
                    col_widths=(12, 16, 7, 18, 9, 9, 6, 8, 7, 8),
                    summary_rows=[
                        (
                            "TOTAL",
                            f"{len(rows)} row(s)",
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            "-",
                            total_overdue,
                        )
                    ],
                )
            ],
            landscape=True,
        )
        return build_pdf_response(f"overdue_installments_{as_of.isoformat()}.pdf", pdf_bytes)


# ---------------------------------------------------------------------------
# 5. Collection & Receipt Register (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class CollectionReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "reports/collection_report.html"
    allowed_roles = FINANCIAL_REPORT_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        date_from = _parse_optional_date(request.GET.get("date_from"))
        date_to = _parse_optional_date(request.GET.get("date_to"))
        collection_centre_id = _accessible_centre_id(
            request.user, request.GET.get("collection_centre")
        )
        admission_centre_id = _accessible_centre_id(
            request.user, request.GET.get("admission_centre")
        )
        course_id = _valid_pk(Course.objects.all(), request.GET.get("course"))
        mode = request.GET.get("mode", "")
        if mode not in PaymentMode.values:
            mode = ""
        collector_id = _valid_pk(User.objects.all(), request.GET.get("collector"))
        status = request.GET.get("status", "")
        if status not in dict(Payment.Status.choices):
            status = ""
        query = (request.GET.get("q") or "").strip()[:100]
        return {
            "date_from": date_from,
            "date_to": date_to,
            "collection_centre_id": collection_centre_id,
            "admission_centre_id": admission_centre_id,
            "course_id": course_id,
            "mode": mode,
            "collector_id": collector_id,
            "status": status,
            "query": query,
            "raw": {
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
                "collection_centre": str(collection_centre_id) if collection_centre_id else "",
                "admission_centre": str(admission_centre_id) if admission_centre_id else "",
                "course": str(course_id) if course_id else "",
                "mode": mode,
                "collector": str(collector_id) if collector_id else "",
                "status": status,
                "q": query,
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_collection_report(
            request.user,
            date_from=f["date_from"],
            date_to=f["date_to"],
            collection_centre_id=f["collection_centre_id"],
            admission_centre_id=f["admission_centre_id"],
            course_id=f["course_id"],
            mode=f["mode"],
            collector_id=f["collector_id"],
            status=f["status"],
            query=f["query"],
        )
        page_obj, page_range, total_count = _paginate(request, report.payments)
        return render(
            request,
            self.template_name,
            {
                "report": report,
                "payments": page_obj.object_list,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
                "course_options": Course.objects.order_by("code"),
                "mode_options": PaymentMode.choices,
                "status_options": Payment.Status.choices,
                "collector_options": User.objects.filter(role__in=FINANCIAL_REPORT_ROLES).order_by(
                    "username"
                ),
            },
        )


class CollectionReportCsvView(CollectionReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_collection_report(
            request.user,
            date_from=f["date_from"],
            date_to=f["date_to"],
            collection_centre_id=f["collection_centre_id"],
            admission_centre_id=f["admission_centre_id"],
            course_id=f["course_id"],
            mode=f["mode"],
            collector_id=f["collector_id"],
            status=f["status"],
            query=f["query"],
        )
        headers = [
            "Receipt Number",
            "Receipt Status",
            "Payment Date",
            "Student ID",
            "Student Name",
            "Course",
            "Batch",
            "Admission Centre",
            "Collection Centre",
            "Mode",
            "Reference",
            "Collector",
            "Amount (INR)",
            "Payment Status",
        ]
        rows = [
            [
                p.receipt.number if hasattr(p, "receipt") else "",
                p.receipt.get_status_display() if hasattr(p, "receipt") else "",
                p.paid_on,
                p.admission.student.student_id or "",
                p.admission.student.get_full_name(),
                p.admission.course.code,
                p.admission.batch.code,
                p.admission.centre.code,
                p.centre.code,
                p.get_mode_display(),
                p.reference,
                str(p.recorded_by),
                p.amount,
                p.get_status_display(),
            ]
            for p in report.payments
        ]
        return build_csv_response(
            f"collection_register_{timezone.localdate().isoformat()}.csv",
            headers,
            rows,
            metadata_rows=[
                ["Report", "Period-Wise Collection & Receipt Register"],
                ["Generated At", timezone.localtime()],
                ["Posted Receipts Count", report.posted_count],
                ["Posted Total (INR)", report.posted_total],
                ["Reversed Count", report.reversed_count],
                ["Reversed Total (INR)", report.reversed_total],
            ],
        )


class CollectionReportPdfView(CollectionReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        report = build_collection_report(
            request.user,
            date_from=f["date_from"],
            date_to=f["date_to"],
            collection_centre_id=f["collection_centre_id"],
            admission_centre_id=f["admission_centre_id"],
            course_id=f["course_id"],
            mode=f["mode"],
            collector_id=f["collector_id"],
            status=f["status"],
            query=f["query"],
        )
        coll_centre_rows = [
            (r.code, r.label, r.posted_count, r.posted_amount, r.reversed_count, r.reversed_amount)
            for r in report.by_collection_centre
        ]
        adm_centre_rows = [
            (r.code, r.label, r.posted_count, r.posted_amount, r.reversed_count, r.reversed_amount)
            for r in report.by_admission_centre
        ]
        register_rows = [
            (
                p.receipt.number if hasattr(p, "receipt") else f"#{p.pk}",
                p.paid_on,
                p.admission.student.student_id or "-",
                p.admission.student.get_full_name(),
                p.admission.centre.code,
                p.centre.code,
                p.get_mode_display(),
                str(p.recorded_by),
                p.amount,
                p.get_status_display(),
            )
            for p in report.payments
        ]
        pdf_bytes = build_pdf_document(
            title="Collection & Receipt Register",
            subtitle=(
                f"Posted: INR {report.posted_total:.2f} ({report.posted_count} receipts) | "
                f"Reversed: INR {report.reversed_total:.2f} ({report.reversed_count})"
            ),
            sections=[
                PdfTableSection(
                    title="Summary by Collection Centre (Where Money Was Received)",
                    headers=(
                        "Centre Code",
                        "Centre Name",
                        "Posted Count",
                        "Posted (INR)",
                        "Reversed Count",
                        "Reversed (INR)",
                    ),
                    rows=coll_centre_rows,
                    col_widths=(15, 30, 13, 15, 13, 14),
                ),
                PdfTableSection(
                    title="Summary by Admission Centre (Student Home Centre)",
                    headers=(
                        "Centre Code",
                        "Centre Name",
                        "Posted Count",
                        "Posted (INR)",
                        "Reversed Count",
                        "Reversed (INR)",
                    ),
                    rows=adm_centre_rows,
                    col_widths=(15, 30, 13, 15, 13, 14),
                ),
                PdfTableSection(
                    title="Receipt Register",
                    headers=(
                        "Receipt #",
                        "Paid On",
                        "Student ID",
                        "Student Name",
                        "Adm Ctr",
                        "Coll Ctr",
                        "Mode",
                        "Collector",
                        "Amount",
                        "Status",
                    ),
                    rows=register_rows,
                    col_widths=(16, 9, 11, 16, 6, 6, 9, 9, 10, 8),
                ),
            ],
            landscape=True,
        )
        return build_pdf_response(
            f"collection_register_{timezone.localdate().isoformat()}.pdf", pdf_bytes
        )


# ---------------------------------------------------------------------------
# 6. Reversals, Refunds & Fee-Plan Reassignments Report (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class AdjustmentsReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "reports/adjustments_report.html"
    allowed_roles = FINANCIAL_REPORT_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        status = request.GET.get("status", "")
        if status not in ("PENDING", "APPROVED", "REJECTED"):
            status = ""
        date_from = _parse_optional_date(request.GET.get("date_from"))
        date_to = _parse_optional_date(request.GET.get("date_to"))
        return {
            "centre_id": centre_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "raw": {
                "centre": str(centre_id) if centre_id else "",
                "status": status,
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        data = build_adjustments_report(
            request.user,
            centre_id=f["centre_id"],
            status=f["status"],
            date_from=f["date_from"],
            date_to=f["date_to"],
        )
        return render(
            request,
            self.template_name,
            {
                **data,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
            },
        )


class AdjustmentsReportCsvView(AdjustmentsReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        data = build_adjustments_report(
            request.user,
            centre_id=f["centre_id"],
            status=f["status"],
            date_from=f["date_from"],
            date_to=f["date_to"],
        )
        headers = [
            "Type",
            "Student ID",
            "Student Name",
            "Centre",
            "Reference",
            "Amount / Difference (INR)",
            "Status",
            "Requested By",
            "Requested At",
            "Decided By",
            "Reason",
        ]
        rows: list[list[Any]] = []
        for rev in data["reversals"]:
            rows.append(
                [
                    "Payment Reversal",
                    rev.payment.admission.student.student_id or "",
                    rev.payment.admission.student.get_full_name(),
                    rev.payment.admission.centre.code,
                    rev.payment.receipt.number
                    if hasattr(rev.payment, "receipt")
                    else f"#{rev.payment_id}",
                    rev.amount,
                    rev.get_status_display(),
                    str(rev.requested_by),
                    rev.requested_at,
                    str(rev.decided_by) if rev.decided_by else "",
                    rev.reason,
                ]
            )
        for ref in data["refunds"]:
            rows.append(
                [
                    "Advance Credit Refund",
                    ref.admission.student.student_id or "",
                    ref.admission.student.get_full_name(),
                    ref.admission.centre.code,
                    f"Credit Lot #{ref.credit_id}",
                    ref.amount,
                    ref.get_status_display(),
                    str(ref.requested_by),
                    ref.requested_at,
                    str(ref.decided_by) if ref.decided_by else "",
                    ref.reason,
                ]
            )
        for rea in data["reassignments"]:
            rows.append(
                [
                    "Fee Plan Reassignment",
                    rea.admission.student.student_id or "",
                    rea.admission.student.get_full_name(),
                    rea.admission.centre.code,
                    f"v{rea.from_plan.version} -> v{rea.to_plan.version}",
                    rea.difference,
                    rea.get_status_display(),
                    str(rea.requested_by),
                    rea.requested_at,
                    str(rea.decided_by) if rea.decided_by else "",
                    rea.reason,
                ]
            )
        return build_csv_response(
            f"reversals_refunds_reassignments_{timezone.localdate().isoformat()}.csv",
            headers,
            rows,
            metadata_rows=[["Report", "Reversals, Refunds & Fee-Plan Reassignments"]],
        )


class AdjustmentsReportPdfView(AdjustmentsReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        data = build_adjustments_report(
            request.user,
            centre_id=f["centre_id"],
            status=f["status"],
            date_from=f["date_from"],
            date_to=f["date_to"],
        )
        pdf_bytes = build_pdf_document(
            title="Reversals, Refunds & Fee-Plan Reassignments Report",
            subtitle=f"Generated on {timezone.localdate().strftime('%d %b %Y')}",
            sections=[
                PdfTableSection(
                    title="Payment Reversals",
                    headers=(
                        "Receipt #",
                        "Student",
                        "Centre",
                        "Amount",
                        "Status",
                        "Requested By",
                        "Decided By",
                        "Reason",
                    ),
                    rows=[
                        (
                            r.payment.receipt.number
                            if hasattr(r.payment, "receipt")
                            else f"#{r.payment_id}",
                            r.payment.admission.student.get_full_name(),
                            r.payment.admission.centre.code,
                            r.amount,
                            r.get_status_display(),
                            str(r.requested_by),
                            str(r.decided_by) if r.decided_by else "-",
                            r.reason,
                        )
                        for r in data["reversals"]
                    ],
                ),
                PdfTableSection(
                    title="Advance Credit Refunds",
                    headers=(
                        "Lot",
                        "Student",
                        "Centre",
                        "Amount",
                        "Mode",
                        "Status",
                        "Decided By",
                        "Reason",
                    ),
                    rows=[
                        (
                            f"Lot #{r.credit_id}",
                            r.admission.student.get_full_name(),
                            r.admission.centre.code,
                            r.amount,
                            r.get_mode_display(),
                            r.get_status_display(),
                            str(r.decided_by) if r.decided_by else "-",
                            r.reason,
                        )
                        for r in data["refunds"]
                    ],
                ),
                PdfTableSection(
                    title="Fee-Plan Reassignments",
                    headers=(
                        "Plans",
                        "Student",
                        "Centre",
                        "Old Total",
                        "New Total",
                        "Diff",
                        "Status",
                        "Reason",
                    ),
                    rows=[
                        (
                            f"v{r.from_plan.version}->v{r.to_plan.version}",
                            r.admission.student.get_full_name(),
                            r.admission.centre.code,
                            r.previous_total,
                            r.proposed_total,
                            r.difference,
                            r.get_status_display(),
                            r.reason,
                        )
                        for r in data["reassignments"]
                    ],
                ),
            ],
            landscape=True,
        )
        return build_pdf_response(
            f"reversals_refunds_reassignments_{timezone.localdate().isoformat()}.pdf", pdf_bytes
        )


# ---------------------------------------------------------------------------
# 7. Financial & Due-Date Audit Trail Viewer (HTML + CSV + PDF)
# ---------------------------------------------------------------------------
class AuditTrailReportView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "reports/audit_trail.html"
    allowed_roles = FINANCIAL_REPORT_ROLES

    def _extract_filters(self, request: HttpRequest) -> dict[str, Any]:
        centre_id = _accessible_centre_id(request.user, request.GET.get("centre"))
        event_type = request.GET.get("event_type", "")
        if event_type not in dict(FinancialAuditEvent.Event.choices):
            event_type = ""
        actor_id = _valid_pk(User.objects.all(), request.GET.get("actor"))
        date_from = _parse_optional_date(request.GET.get("date_from"))
        date_to = _parse_optional_date(request.GET.get("date_to"))
        query = (request.GET.get("q") or "").strip()[:100]
        return {
            "centre_id": centre_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "date_from": date_from,
            "date_to": date_to,
            "query": query,
            "raw": {
                "centre": str(centre_id) if centre_id else "",
                "event_type": event_type,
                "actor": str(actor_id) if actor_id else "",
                "date_from": date_from.isoformat() if date_from else "",
                "date_to": date_to.isoformat() if date_to else "",
                "q": query,
            },
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = audit_events_queryset(
            request.user,
            centre_id=f["centre_id"],
            event_type=f["event_type"],
            actor_id=f["actor_id"],
            date_from=f["date_from"],
            date_to=f["date_to"],
            query=f["query"],
        )
        page_obj, page_range, total_count = _paginate(request, qs)
        due_date_revisions = InstallmentDueDateRevision.objects.filter(
            installment__fee_plan__admission__centre__in=request.user.get_accessible_centres()
        ).select_related(
            "installment__fee_plan__admission__student",
            "installment__fee_plan__admission__centre",
            "revised_by",
        )[:25]
        return render(
            request,
            self.template_name,
            {
                "events": page_obj.object_list,
                "due_date_revisions": due_date_revisions,
                "page_obj": page_obj,
                "page_range": page_range,
                "total_count": total_count,
                "filters": f["raw"],
                "querystring": _urlencode_filters(f["raw"]),
                "centre_options": request.user.get_accessible_centres(),
                "event_type_options": FinancialAuditEvent.Event.choices,
                "actor_options": User.objects.order_by("username"),
            },
        )


class AuditTrailCsvView(AuditTrailReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = audit_events_queryset(
            request.user,
            centre_id=f["centre_id"],
            event_type=f["event_type"],
            actor_id=f["actor_id"],
            date_from=f["date_from"],
            date_to=f["date_to"],
            query=f["query"],
        )
        headers = [
            "Timestamp",
            "Event Type",
            "Centre",
            "Student ID",
            "Student Name",
            "Actor",
            "Amount (INR)",
            "Receipt #",
            "Detail",
        ]
        rows = [
            [
                ev.created_at,
                ev.get_event_type_display(),
                ev.admission.centre.code,
                ev.admission.student.student_id or "",
                ev.admission.student.get_full_name(),
                str(ev.actor),
                ev.amount,
                ev.payment.receipt.number
                if ev.payment_id and hasattr(ev.payment, "receipt")
                else "",
                ev.detail,
            ]
            for ev in qs
        ]
        return build_csv_response(
            f"financial_audit_trail_{timezone.localdate().isoformat()}.csv",
            headers,
            rows,
            metadata_rows=[["Report", "Append-Only Financial Audit Trail"]],
        )


class AuditTrailPdfView(AuditTrailReportView):
    def get(self, request: HttpRequest) -> HttpResponse:
        f = self._extract_filters(request)
        qs = list(
            audit_events_queryset(
                request.user,
                centre_id=f["centre_id"],
                event_type=f["event_type"],
                actor_id=f["actor_id"],
                date_from=f["date_from"],
                date_to=f["date_to"],
                query=f["query"],
            )[:300]
        )
        rows = [
            (
                ev.created_at,
                ev.get_event_type_display(),
                ev.admission.centre.code,
                ev.admission.student.student_id or ev.admission.student.get_full_name(),
                str(ev.actor),
                ev.amount,
                ev.detail,
            )
            for ev in qs
        ]
        today_str = timezone.localdate().strftime("%d %b %Y")
        pdf_bytes = build_pdf_document(
            title="Append-Only Financial Audit Trail",
            subtitle=f"Showing {len(rows)} event(s) | Generated on {today_str}",
            sections=[
                PdfTableSection(
                    title="Financial Audit Events",
                    headers=(
                        "Timestamp",
                        "Event",
                        "Centre",
                        "Student",
                        "Actor",
                        "Amount",
                        "Detail",
                    ),
                    rows=rows,
                    col_widths=(14, 15, 7, 14, 10, 10, 30),
                )
            ],
            landscape=True,
        )
        return build_pdf_response(
            f"financial_audit_trail_{timezone.localdate().isoformat()}.pdf", pdf_bytes
        )

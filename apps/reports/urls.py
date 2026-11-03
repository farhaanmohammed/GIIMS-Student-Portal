from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportsHubView.as_view(), name="report_hub"),
    # Student Fee Ledger
    path("ledger/", views.StudentLedgerIndexView.as_view(), name="ledger_index"),
    path(
        "ledger/<int:admission_id>/", views.StudentLedgerDetailView.as_view(), name="student_ledger"
    ),
    path(
        "ledger/<int:admission_id>/csv/",
        views.StudentLedgerCsvView.as_view(),
        name="student_ledger_csv",
    ),
    path(
        "ledger/<int:admission_id>/pdf/",
        views.StudentLedgerPdfView.as_view(),
        name="student_ledger_pdf",
    ),
    # Student & Admission Register
    path("admissions/", views.AdmissionRegisterReportView.as_view(), name="admission_register"),
    path(
        "admissions/csv/",
        views.AdmissionRegisterCsvView.as_view(),
        name="admission_register_csv",
    ),
    path(
        "admissions/pdf/",
        views.AdmissionRegisterPdfView.as_view(),
        name="admission_register_pdf",
    ),
    # Outstanding Report
    path("outstanding/", views.OutstandingReportView.as_view(), name="outstanding_report"),
    path(
        "outstanding/csv/",
        views.OutstandingReportCsvView.as_view(),
        name="outstanding_report_csv",
    ),
    path(
        "outstanding/pdf/",
        views.OutstandingReportPdfView.as_view(),
        name="outstanding_report_pdf",
    ),
    # Overdue Installments Report
    path("overdue/", views.OverdueInstallmentsReportView.as_view(), name="overdue_report"),
    path("overdue/csv/", views.OverdueInstallmentsCsvView.as_view(), name="overdue_report_csv"),
    path("overdue/pdf/", views.OverdueInstallmentsPdfView.as_view(), name="overdue_report_pdf"),
    # Collection & Receipt Register
    path("collections/", views.CollectionReportView.as_view(), name="collection_report"),
    path("collections/csv/", views.CollectionReportCsvView.as_view(), name="collection_report_csv"),
    path("collections/pdf/", views.CollectionReportPdfView.as_view(), name="collection_report_pdf"),
    # Reversals, Refunds & Reassignments Report
    path("adjustments/", views.AdjustmentsReportView.as_view(), name="adjustments_report"),
    path(
        "adjustments/csv/", views.AdjustmentsReportCsvView.as_view(), name="adjustments_report_csv"
    ),
    path(
        "adjustments/pdf/", views.AdjustmentsReportPdfView.as_view(), name="adjustments_report_pdf"
    ),
    # Audit Trail Viewer
    path("audit/", views.AuditTrailReportView.as_view(), name="audit_trail"),
    path("audit/csv/", views.AuditTrailCsvView.as_view(), name="audit_trail_csv"),
    path("audit/pdf/", views.AuditTrailPdfView.as_view(), name="audit_trail_pdf"),
]

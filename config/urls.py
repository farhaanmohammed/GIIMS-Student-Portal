"""
URL configuration for config project.
"""

from django.contrib import admin
from django.urls import include, path

from config.views import dashboard_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("academics/", include("apps.academics.urls")),
    path("students/", include("apps.students.urls")),
    path("admissions/", include("apps.admissions.urls")),
    path("fees/", include("apps.fees.urls")),
    path("payments/", include("apps.payments.urls")),
    path("reports/", include("apps.reports.urls")),
    path("", dashboard_view, name="dashboard"),
]

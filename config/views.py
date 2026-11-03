from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.reports.selectors import dashboard_summary


@login_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """
    Renders the GIIMS Student Portal administrative dashboard with live,
    centre-scoped admissions and financial metrics for the authenticated user.
    """
    summary = dashboard_summary(request.user)
    return render(request, "dashboard.html", {"summary": summary, **summary})

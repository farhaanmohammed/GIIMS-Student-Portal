from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """
    Renders the initial GIIMS Student Portal administrative dashboard.
    Requires an authenticated user session.
    """
    return render(request, "dashboard.html")

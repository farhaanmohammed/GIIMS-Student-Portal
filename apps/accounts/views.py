from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy

from apps.accounts.forms import GIIMSAuthenticationForm


class GIIMSLoginView(LoginView):
    """
    Renders institutional authentication screen.
    """

    template_name = "accounts/login.html"
    form_class = GIIMSAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        return reverse_lazy("dashboard")


class GIIMSLogoutView(LogoutView):
    """
    Terminates user session securely with POST/GET support.
    """

    next_page = reverse_lazy("login")

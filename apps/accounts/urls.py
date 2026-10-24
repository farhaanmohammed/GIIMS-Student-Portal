from django.urls import path

from apps.accounts.views import GIIMSLoginView, GIIMSLogoutView

urlpatterns = [
    path("login/", GIIMSLoginView.as_view(), name="login"),
    path("logout/", GIIMSLogoutView.as_view(), name="logout"),
]

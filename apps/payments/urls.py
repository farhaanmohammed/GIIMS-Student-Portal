from django.urls import path

from apps.payments import views

app_name = "payments"

urlpatterns = [
    path("", views.PaymentListView.as_view(), name="payment_list"),
    path("collect/", views.PaymentCreateView.as_view(), name="payment_create"),
    path("<int:pk>/", views.PaymentDetailView.as_view(), name="payment_detail"),
    path(
        "credit/apply/",
        views.CreditApplicationView.as_view(),
        name="credit_apply",
    ),
    path("refunds/request/", views.RefundRequestView.as_view(), name="refund_request"),
    path("refunds/", views.RefundRequestListView.as_view(), name="refund_list"),
    path("refunds/<int:pk>/decide/", views.RefundDecideView.as_view(), name="refund_decide"),
    path(
        "reversals/request/<int:pk>/",
        views.ReversalRequestView.as_view(),
        name="reversal_request",
    ),
    path("reversals/", views.ReversalRequestListView.as_view(), name="reversal_list"),
    path(
        "reversals/<int:pk>/decide/",
        views.ReversalDecideView.as_view(),
        name="reversal_decide",
    ),
]

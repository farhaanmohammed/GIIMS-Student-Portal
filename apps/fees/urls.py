from django.urls import path

from apps.fees import views

app_name = "fees"

urlpatterns = [
    path("plans/", views.FeePlanListView.as_view(), name="fee_plan_list"),
    path("plans/new/", views.FeePlanCreateView.as_view(), name="fee_plan_create"),
    path("plans/<int:pk>/", views.FeePlanDetailView.as_view(), name="fee_plan_detail"),
    path("plans/<int:pk>/edit/", views.FeePlanUpdateView.as_view(), name="fee_plan_update"),
    path("plans/<int:pk>/approve/", views.FeePlanApproveView.as_view(), name="fee_plan_approve"),
    path(
        "plans/<int:pk>/new-version/",
        views.FeePlanVersionView.as_view(),
        name="fee_plan_new_version",
    ),
    path("plans/<int:pk>/cancel/", views.FeePlanCancelView.as_view(), name="fee_plan_cancel"),
    path(
        "installments/<int:pk>/due-date/",
        views.InstallmentDueDateView.as_view(),
        name="installment_due_date",
    ),
    path(
        "due-date-requests/",
        views.DueDateRequestListView.as_view(),
        name="due_date_request_list",
    ),
    path(
        "due-date-requests/<int:pk>/decide/",
        views.DueDateRequestDecideView.as_view(),
        name="due_date_request_decide",
    ),
]

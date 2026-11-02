from django.urls import path

from apps.admissions import views

app_name = "admissions"

urlpatterns = [
    path("", views.AdmissionListView.as_view(), name="admission_list"),
    path("enquire/", views.EnquiryCreateView.as_view(), name="enquiry_create"),
    path("<int:pk>/", views.AdmissionDetailView.as_view(), name="admission_detail"),
    path("<int:pk>/enroll/", views.EnrollView.as_view(), name="enroll"),
]

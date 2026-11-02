from django.urls import path

from apps.students import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="student_list"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="student_detail"),
]

from django.urls import path

from apps.academics import views

app_name = "academics"

urlpatterns = [
    path("academic-years/", views.AcademicYearListView.as_view(), name="academic_year_list"),
    path(
        "academic-years/new/",
        views.AcademicYearCreateView.as_view(),
        name="academic_year_create",
    ),
    path(
        "academic-years/<int:pk>/edit/",
        views.AcademicYearUpdateView.as_view(),
        name="academic_year_update",
    ),
    path(
        "academic-years/<int:pk>/toggle/",
        views.AcademicYearToggleView.as_view(),
        name="academic_year_toggle",
    ),
    path("courses/", views.CourseListView.as_view(), name="course_list"),
    path("courses/new/", views.CourseCreateView.as_view(), name="course_create"),
    path("courses/<int:pk>/edit/", views.CourseUpdateView.as_view(), name="course_update"),
    path("courses/<int:pk>/toggle/", views.CourseToggleView.as_view(), name="course_toggle"),
    path("batches/", views.BatchListView.as_view(), name="batch_list"),
    path("batches/new/", views.BatchCreateView.as_view(), name="batch_create"),
    path("batches/<int:pk>/edit/", views.BatchUpdateView.as_view(), name="batch_update"),
    path("batches/<int:pk>/toggle/", views.BatchToggleView.as_view(), name="batch_toggle"),
]

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import DetailView, ListView

from apps.core.permissions import CentreAccessRequiredMixin
from apps.students.models import Student

ADMISSION_RELATIONS = (
    "admissions",
    "admissions__centre",
    "admissions__course",
    "admissions__batch",
    "admissions__academic_year",
)


def _students_for_user(user):
    """Students reachable through admissions in the user's accessible centres."""
    return (
        Student.objects.filter(admissions__centre__in=user.get_accessible_centres())
        .distinct()
        .prefetch_related(*ADMISSION_RELATIONS)
    )


class StudentListView(LoginRequiredMixin, CentreAccessRequiredMixin, ListView):
    model = Student
    template_name = "students/student_list.html"
    context_object_name = "students"
    paginate_by = 25

    def get_queryset(self):
        qs = _students_for_user(self.request.user)

        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(student_id__icontains=query)
                | Q(phone__icontains=query)
                | Q(guardian_name__icontains=query)
            )

        enrolled = self.request.GET.get("enrolled")
        if enrolled == "1":
            qs = qs.filter(student_id__isnull=False)
        elif enrolled == "0":
            qs = qs.filter(student_id__isnull=True)

        return qs.order_by("last_name", "first_name")


class StudentDetailView(LoginRequiredMixin, CentreAccessRequiredMixin, DetailView):
    model = Student
    template_name = "students/student_detail.html"
    context_object_name = "student"

    def get_queryset(self):
        return _students_for_user(self.request.user)

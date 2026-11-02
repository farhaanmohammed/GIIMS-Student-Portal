from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import DetailView, ListView

from apps.core.permissions import CentreAccessRequiredMixin
from apps.students.models import Student


class StudentListView(LoginRequiredMixin, CentreAccessRequiredMixin, ListView):
    model = Student
    template_name = "students/student_list.html"
    context_object_name = "students"
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        accessible_centres = user.get_accessible_centres()
        qs = (
            Student.objects.filter(admissions__centre__in=accessible_centres)
            .distinct()
            .prefetch_related("admissions__centre", "admissions__course", "admissions__batch", "admissions__academic_year")
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(student_id__icontains=q)
                | Q(phone__icontains=q)
                | Q(guardian_name__icontains=q)
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
        user = self.request.user
        accessible_centres = user.get_accessible_centres()
        return (
            Student.objects.filter(admissions__centre__in=accessible_centres)
            .distinct()
            .prefetch_related("admissions__centre", "admissions__course", "admissions__batch", "admissions__academic_year")
        )

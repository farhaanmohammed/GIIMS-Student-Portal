from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.admissions.forms import AdmissionEnquiryForm, StudentEnquiryForm
from apps.admissions.models import Admission
from apps.admissions.services import enroll_admission
from apps.core.permissions import CentreAccessRequiredMixin

ENQUIRY_WRITE_ROLES = ("ADMIN", "MANAGEMENT", "ADMISSIONS")


def _can_write_admissions(user) -> bool:
    """Admissions writes are limited to privileged operational roles."""
    return bool(user.is_superuser or user.role in ENQUIRY_WRITE_ROLES)


class AdmissionListView(LoginRequiredMixin, CentreAccessRequiredMixin, ListView):
    model = Admission
    template_name = "admissions/admission_list.html"
    context_object_name = "admissions"
    paginate_by = 25

    def get_queryset(self):
        accessible_centres = self.request.user.get_accessible_centres()
        qs = (
            Admission.objects.filter(centre__in=accessible_centres)
            .select_related("student", "centre", "course", "batch", "academic_year")
            .order_by("-enquired_at")
        )

        status = self.request.GET.get("status")
        if status in dict(Admission.Status.choices):
            qs = qs.filter(status=status)

        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(student__first_name__icontains=query)
                | Q(student__last_name__icontains=query)
                | Q(student__student_id__icontains=query)
                | Q(student__phone__icontains=query)
            )
        return qs


class AdmissionDetailView(LoginRequiredMixin, CentreAccessRequiredMixin, DetailView):
    model = Admission
    template_name = "admissions/admission_detail.html"
    context_object_name = "admission"

    def get_queryset(self):
        accessible_centres = self.request.user.get_accessible_centres()
        return Admission.objects.filter(centre__in=accessible_centres).select_related(
            "student", "centre", "course", "batch", "academic_year", "enrolled_by"
        )


class EnquiryCreateView(LoginRequiredMixin, View):
    """Create a durable Student and its first Admission in one atomic step."""

    template_name = "admissions/enquiry_form.html"

    def _render(self, request, student_form, admission_form):
        return render(
            request,
            self.template_name,
            {"student_form": student_form, "admission_form": admission_form},
        )

    def get(self, request):
        if not _can_write_admissions(request.user):
            messages.error(request, "You do not have permission to create enquiries.")
            return redirect("admissions:admission_list")
        return self._render(
            request,
            StudentEnquiryForm(),
            AdmissionEnquiryForm(user=request.user),
        )

    def post(self, request):
        user = request.user
        if not _can_write_admissions(user):
            messages.error(request, "You do not have permission to create enquiries.")
            return redirect("admissions:admission_list")

        student_form = StudentEnquiryForm(request.POST)
        admission_form = AdmissionEnquiryForm(request.POST, user=user)
        if not (student_form.is_valid() and admission_form.is_valid()):
            return self._render(request, student_form, admission_form)

        with transaction.atomic():
            student = student_form.save()
            admission = admission_form.save(commit=False)
            admission.student = student
            admission.status = Admission.Status.ENQUIRED
            admission.save()

        messages.success(request, "Enquiry created successfully.")
        return redirect("admissions:admission_detail", pk=admission.pk)


class EnrollView(LoginRequiredMixin, View):
    """POST-only enrollment transition that assigns the permanent student ID."""

    def post(self, request, pk):
        user = request.user
        admission = get_object_or_404(
            Admission.objects.select_related(
                "student", "centre", "course", "batch", "academic_year"
            ),
            pk=pk,
            centre__in=user.get_accessible_centres(),
        )

        if not _can_write_admissions(user):
            messages.error(request, "You do not have permission to enroll students.")
            return redirect("admissions:admission_detail", pk=pk)

        try:
            enroll_admission(admission, user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Student enrolled successfully. Student ID assigned.")
        return redirect("admissions:admission_detail", pk=pk)

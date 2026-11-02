from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views import View

from apps.admissions.forms import AdmissionEnquiryForm, StudentEnquiryForm
from apps.admissions.models import Admission
from apps.admissions.services import enroll_admission
from apps.core.permissions import CentreAccessRequiredMixin, role_required


class AdmissionListView(LoginRequiredMixin, CentreAccessRequiredMixin, ListView):
    model = Admission
    template_name = "admissions/admission_list.html"
    context_object_name = "admissions"
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        accessible_centres = user.get_accessible_centres()
        qs = (
            Admission.objects.filter(centre__in=accessible_centres)
            .select_related("student", "centre", "course", "batch", "academic_year")
            .order_by("-enquired_at")
        )
        status = self.request.GET.get("status")
        if status in dict(Admission.Status.choices):
            qs = qs.filter(status=status)
        q = self.request.GET.get("q", "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(student__first_name__icontains=q)
                | Q(student__last_name__icontains=q)
                | Q(student__student_id__icontains=q)
                | Q(student__phone__icontains=q)
            )
        return qs


class AdmissionDetailView(LoginRequiredMixin, CentreAccessRequiredMixin, DetailView):
    model = Admission
    template_name = "admissions/admission_detail.html"
    context_object_name = "admission"

    def get_queryset(self):
        user = self.request.user
        accessible_centres = user.get_accessible_centres()
        return Admission.objects.filter(centre__in=accessible_centres).select_related(
            "student", "centre", "course", "batch", "academic_year", "enrolled_by"
        )


class EnquiryCreateView(LoginRequiredMixin, View):
    template_name = "admissions/enquiry_form.html"

    def get(self, request):
        user = request.user
        if user.role not in ("ADMIN", "MANAGEMENT", "ADMISSIONS") and not user.is_superuser:
            messages.error(request, "You do not have permission to create enquiries.")
            return redirect("admissions:admission_list")
        student_form = StudentEnquiryForm()
        admission_form = AdmissionEnquiryForm(user=user)
        return render(request, self.template_name, {"student_form": student_form, "admission_form": admission_form})

    def post(self, request):
        user = request.user
        if user.role not in ("ADMIN", "MANAGEMENT", "ADMISSIONS") and not user.is_superuser:
            messages.error(request, "You do not have permission to create enquiries.")
            return redirect("admissions:admission_list")
        student_form = StudentEnquiryForm(request.POST)
        admission_form = AdmissionEnquiryForm(user=user, data=request.POST)
        if student_form.is_valid() and admission_form.is_valid():
            admission_form._user = user
            if not user.can_access_centre(admission_form.cleaned_data["centre"]):
                messages.error(request, "You do not have access to this centre.")
                return render(request, self.template_name, {"student_form": student_form, "admission_form": admission_form})
            with transaction.atomic():
                student = student_form.save()
                admission = admission_form.save(commit=False)
                admission.student = student
                admission.status = Admission.Status.ENQUIRED
                admission.save()
            messages.success(request, "Enquiry created successfully.")
            return redirect("admissions:admission_detail", pk=admission.pk)
        return render(request, self.template_name, {"student_form": student_form, "admission_form": admission_form})


class EnrollView(LoginRequiredMixin, View):
    def post(self, request, pk):
        user = request.user
        accessible_centres = user.get_accessible_centres()
        admission = get_object_or_404(
            Admission.objects.select_related("student", "centre", "course", "batch", "academic_year"),
            pk=pk,
            centre__in=accessible_centres,
        )
        # Only ADMIN/MANAGEMENT/ADMISSIONS can enroll
        if user.role not in ("ADMIN", "MANAGEMENT", "ADMISSIONS"):
            messages.error(request, "You do not have permission to enroll students.")
            return redirect("admissions:admission_detail", pk=pk)
        try:
            enroll_admission(admission, user)
            messages.success(request, "Student enrolled successfully. Student ID assigned.")
        except ValueError as e:
            messages.error(request, str(e))
        except Exception:
            messages.error(request, "Error enrolling student.")
        return redirect("admissions:admission_detail", pk=pk)

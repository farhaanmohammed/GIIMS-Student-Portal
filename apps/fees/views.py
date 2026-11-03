from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from apps.admissions.models import Admission
from apps.core.permissions import role_required
from apps.fees.forms import DueDateChangeForm, DueDateDecisionForm, FeePlanForm
from apps.fees.models import (
    DueDateChangeRequest,
    FeeInstallment,
    FeePlan,
    InstallmentDueDateRevision,
)
from apps.fees.selectors import (
    admission_fee_summary,
    effective_due_date,
    installment_outstanding,
    pending_due_date_requests,
    plan_history,
)
from apps.fees.services import (
    apply_due_date_revision,
    approve_fee_plan,
    cancel_draft_fee_plan,
    create_draft_fee_plan,
    create_fee_plan_version,
    decide_due_date_change,
    request_due_date_change,
    update_draft_fee_plan,
)

PAGE_SIZE = 25
PLAN_EDITOR_ROLES = ("ADMIN", "MANAGEMENT", "ACCOUNTS")
PLAN_APPROVER_ROLES = ("ADMIN", "MANAGEMENT")
DUE_DATE_APPLIER_ROLES = ("ADMIN", "MANAGEMENT", "ACCOUNTS")


def _fee_plans_for_user(user) -> QuerySet[FeePlan]:
    return FeePlan.objects.filter(
        admission__centre__in=user.get_accessible_centres()
    ).select_related(
        "admission__student",
        "admission__centre",
        "admission__course",
        "admission__batch",
        "approved_by",
    )


def _installments_for_user(user) -> QuerySet[FeeInstallment]:
    return FeeInstallment.objects.filter(
        fee_plan__admission__centre__in=user.get_accessible_centres()
    ).select_related(
        "fee_plan",
        "fee_plan__admission__student",
        "fee_plan__admission__centre",
    )


def _can_edit_plans(user) -> bool:
    """Presentation helper only; services enforce writes."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in PLAN_EDITOR_ROLES)
    )


def _can_approve_plans(user) -> bool:
    """Presentation helper only; services enforce writes."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in PLAN_APPROVER_ROLES)
    )


def _can_apply_due_date(user) -> bool:
    """Presentation helper only; services enforce writes."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in DUE_DATE_APPLIER_ROLES)
    )


def _urlencode_filters(filters: dict[str, str]) -> str:
    return urlencode({key: value for key, value in filters.items() if value})


class RoleRequiredViewMixin:
    """
    Role gate for authenticated views.

    Placed *after* ``LoginRequiredMixin`` in the MRO so anonymous users are
    redirected to login and only authenticated, unauthorized users receive a
    403 response.
    """

    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        checker = role_required(*self.allowed_roles)
        return checker(super().dispatch)(request, *args, **kwargs)


class FeePlanListView(LoginRequiredMixin, ListView):
    model = FeePlan
    template_name = "fees/fee_plan_list.html"
    partial_template_name = "fees/partials/_fee_plan_table.html"
    context_object_name = "fee_plans"
    paginate_by = PAGE_SIZE

    def get_filters(self) -> dict[str, str]:
        cached = getattr(self, "_filters", None)
        if cached is not None:
            return cached
        status = self.request.GET.get("status", "")
        course = self.request.GET.get("course", "")
        filters = {
            "q": (self.request.GET.get("q") or "").strip()[:100],
            "status": status if status in dict(FeePlan.Status.choices) else "",
            "course": course if course.isdigit() else "",
        }
        self._filters = filters
        return filters

    def get_queryset(self) -> QuerySet[FeePlan]:
        queryset = _fee_plans_for_user(self.request.user)
        filters = self.get_filters()
        if filters["q"]:
            queryset = queryset.filter(
                Q(admission__student__student_id__icontains=filters["q"])
                | Q(admission__student__first_name__icontains=filters["q"])
                | Q(admission__student__last_name__icontains=filters["q"])
                | Q(admission__student__phone__icontains=filters["q"])
            )
        if filters["status"]:
            queryset = queryset.filter(status=filters["status"])
        if filters["course"]:
            queryset = queryset.filter(admission__course_id=int(filters["course"]))
        return queryset.order_by("-created_at", "-pk")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["filters"] = self.get_filters()
        context["querystring"] = _urlencode_filters(context["filters"])
        context["can_edit_plans"] = _can_edit_plans(self.request.user)
        context["can_approve_plans"] = _can_approve_plans(self.request.user)
        context["can_manage_plans"] = _can_edit_plans(self.request.user)
        context["status_options"] = FeePlan.Status.choices
        page_obj = context.get("page_obj")
        if page_obj is not None:
            context["total_count"] = page_obj.paginator.count
            context["page_range"] = page_obj.paginator.get_elided_page_range(page_obj.number)
        else:
            context["total_count"] = 0
            context["page_range"] = []
        return context

    def get_template_names(self) -> list[str]:
        if self.partial_template_name and self.request.headers.get("HX-Request"):
            return [self.partial_template_name]
        return [self.template_name]


class FeePlanDetailView(LoginRequiredMixin, DetailView):
    model = FeePlan
    template_name = "fees/fee_plan_detail.html"
    context_object_name = "fee_plan"

    def get_queryset(self) -> QuerySet[FeePlan]:
        return _fee_plans_for_user(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        plan = self.object
        today = timezone.localdate()
        pending_requests = {
            request.installment_id: request
            for request in DueDateChangeRequest.objects.filter(
                installment__fee_plan=plan,
                status=DueDateChangeRequest.Status.PENDING,
            )
        }

        installment_rows = []
        for installment in plan.installments.all():
            due_date = effective_due_date(installment)
            outstanding = installment_outstanding(installment, today)
            installment_rows.append(
                {
                    "installment": installment,
                    "effective_due_date": due_date,
                    "outstanding": outstanding,
                    "is_overdue": due_date is not None and outstanding > 0 and due_date < today,
                    "pending_request": pending_requests.get(installment.pk),
                }
            )

        context.update(
            {
                "installment_rows": installment_rows,
                "summary": admission_fee_summary(plan.admission, today),
                "history": plan_history(plan.admission),
                "due_date_history": InstallmentDueDateRevision.objects.filter(
                    installment__fee_plan=plan
                )
                .select_related("installment", "revised_by", "source_request")
                .order_by("-created_at"),
                "request_history": DueDateChangeRequest.objects.filter(installment__fee_plan=plan)
                .select_related("installment", "requested_by", "decided_by")
                .order_by("-requested_at"),
                "can_edit_plans": _can_edit_plans(self.request.user),
                "can_approve_plans": _can_approve_plans(self.request.user),
                "can_apply_due_date": _can_apply_due_date(self.request.user),
                "today": today,
            }
        )
        return context


class FeePlanCreateView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "fees/fee_plan_form.html"
    allowed_roles = PLAN_EDITOR_ROLES

    def _get_admission(self, request: HttpRequest):
        admission_id = request.GET.get("admission") or request.POST.get("admission")
        if not admission_id or not str(admission_id).isdigit():
            return None
        return get_object_or_404(
            Admission.objects.select_related("student", "centre", "course", "batch"),
            pk=int(admission_id),
            centre__in=request.user.get_accessible_centres(),
        )

    def _render(self, request, admission, form):
        return render(
            request,
            self.template_name,
            {"admission": admission, "form": form, "is_create": True},
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        admission = self._get_admission(request)
        if admission is None:
            messages.error(request, "Select an admission before creating a fee plan.")
            return redirect("fees:fee_plan_list")
        if admission.status != Admission.Status.ENROLLED:
            messages.error(request, "A fee plan can only be created for an enrolled admission.")
            return redirect("admissions:admission_detail", pk=admission.pk)
        return self._render(request, admission, FeePlanForm())

    def post(self, request: HttpRequest) -> HttpResponse:
        admission = self._get_admission(request)
        if admission is None:
            messages.error(request, "Select an admission before creating a fee plan.")
            return redirect("fees:fee_plan_list")
        form = FeePlanForm(request.POST)
        if not form.is_valid():
            return self._render(request, admission, form)

        try:
            plan = create_draft_fee_plan(
                admission,
                course_fee=form.cleaned_data["course_fee"],
                gst_rate=form.cleaned_data["gst_rate"],
                components=form.components(),
                actor=request.user,
                notes=form.cleaned_data["notes"],
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self._render(request, admission, form)

        messages.success(request, f"Draft fee plan v{plan.version} created.")
        return redirect("fees:fee_plan_detail", pk=plan.pk)


class FeePlanUpdateView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "fees/fee_plan_form.html"
    allowed_roles = PLAN_EDITOR_ROLES

    def _get_plan(self, request: HttpRequest, pk: int) -> FeePlan:
        return get_object_or_404(_fee_plans_for_user(request.user), pk=pk)

    def _render(self, request, plan, form):
        return render(
            request,
            self.template_name,
            {
                "admission": plan.admission,
                "fee_plan": plan,
                "form": form,
                "is_create": False,
            },
        )

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        plan = self._get_plan(request, pk)
        if plan.status != FeePlan.Status.DRAFT:
            messages.error(request, "Only draft fee plans can be edited.")
            return redirect("fees:fee_plan_detail", pk=plan.pk)
        return self._render(request, plan, FeePlanForm(plan=plan))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        plan = self._get_plan(request, pk)
        if plan.status != FeePlan.Status.DRAFT:
            messages.error(request, "Only draft fee plans can be edited.")
            return redirect("fees:fee_plan_detail", pk=plan.pk)

        form = FeePlanForm(request.POST, plan=plan)
        if not form.is_valid():
            return self._render(request, plan, form)

        try:
            plan = update_draft_fee_plan(
                plan,
                course_fee=form.cleaned_data["course_fee"],
                gst_rate=form.cleaned_data["gst_rate"],
                components=form.components(),
                notes=form.cleaned_data["notes"],
                actor=request.user,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self._render(request, plan, form)

        messages.success(request, f"Draft fee plan v{plan.version} updated.")
        return redirect("fees:fee_plan_detail", pk=plan.pk)


class PostOnlyMixin:
    """Return 405 for anything other than POST."""

    @method_decorator(require_POST)
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(request, *args, **kwargs)


class FeePlanApproveView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = PLAN_APPROVER_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        plan = get_object_or_404(_fee_plans_for_user(request.user), pk=pk)
        try:
            approve_fee_plan(plan, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Fee plan v{plan.version} approved.")
        return redirect("fees:fee_plan_detail", pk=plan.pk)


class FeePlanVersionView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = PLAN_EDITOR_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        plan = get_object_or_404(_fee_plans_for_user(request.user), pk=pk)
        try:
            new_plan = create_fee_plan_version(plan, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("fees:fee_plan_detail", pk=plan.pk)

        messages.success(
            request,
            f"Draft version {new_plan.version} created. The approved version is unchanged "
            "until the draft is approved.",
        )
        return redirect("fees:fee_plan_detail", pk=new_plan.pk)


class FeePlanCancelView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = PLAN_APPROVER_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        plan = get_object_or_404(_fee_plans_for_user(request.user), pk=pk)
        try:
            cancel_draft_fee_plan(plan, request.user, request.POST.get("reason", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Draft fee plan v{plan.version} cancelled.")
        return redirect("fees:fee_plan_detail", pk=plan.pk)


class InstallmentDueDateView(LoginRequiredMixin, View):
    """
    Apply a due-date revision (privileged roles) or request one (any other
    authenticated role, including admissions).
    """

    template_name = "fees/due_date_form.html"

    def _get_installment(self, request: HttpRequest, pk: int) -> FeeInstallment:
        return get_object_or_404(_installments_for_user(request.user), pk=pk)

    def _render(self, request, installment, form):
        return render(
            request,
            self.template_name,
            {
                "installment": installment,
                "fee_plan": installment.fee_plan,
                "form": form,
                "current_due_date": effective_due_date(installment),
                "can_apply": _can_apply_due_date(request.user),
            },
        )

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        installment = self._get_installment(request, pk)
        if installment.category != FeeInstallment.Category.COURSE:
            messages.error(
                request, "Application and admission charges are stage-based and have no due date."
            )
            return redirect("fees:fee_plan_detail", pk=installment.fee_plan_id)
        return self._render(request, installment, DueDateChangeForm())

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        installment = self._get_installment(request, pk)
        if installment.category != FeeInstallment.Category.COURSE:
            messages.error(
                request, "Application and admission charges are stage-based and have no due date."
            )
            return redirect("fees:fee_plan_detail", pk=installment.fee_plan_id)

        form = DueDateChangeForm(request.POST)
        if not form.is_valid():
            return self._render(request, installment, form)

        proposed_due_date = form.cleaned_data["proposed_due_date"]
        reason = form.cleaned_data["reason"]
        try:
            if _can_apply_due_date(request.user):
                apply_due_date_revision(
                    installment,
                    revised_due_date=proposed_due_date,
                    reason=reason,
                    actor=request.user,
                )
            else:
                request_due_date_change(
                    installment,
                    proposed_due_date=proposed_due_date,
                    reason=reason,
                    actor=request.user,
                )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self._render(request, installment, form)

        if _can_apply_due_date(request.user):
            messages.success(request, "Due-date revision applied and recorded.")
        else:
            messages.success(
                request,
                "Due-date change requested. An authorised approver must decide it.",
            )
        return redirect("fees:fee_plan_detail", pk=installment.fee_plan_id)


class DueDateRequestListView(LoginRequiredMixin, RoleRequiredViewMixin, ListView):
    template_name = "fees/due_date_request_list.html"
    context_object_name = "due_date_requests"
    paginate_by = PAGE_SIZE
    allowed_roles = DUE_DATE_APPLIER_ROLES

    def get_queryset(self):
        return pending_due_date_requests(self.request.user)


class DueDateRequestDecideView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = DUE_DATE_APPLIER_ROLES

    def _get_request(self, user, pk: int) -> DueDateChangeRequest:
        return get_object_or_404(
            DueDateChangeRequest.objects.filter(
                installment__fee_plan__admission__centre__in=user.get_accessible_centres()
            ).select_related("installment"),
            pk=pk,
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        change_request = self._get_request(request.user, pk)
        form = DueDateDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose approve or reject before submitting.")
            return redirect(reverse("fees:due_date_request_list"))

        try:
            decide_due_date_change(
                change_request,
                approve=form.cleaned_data["decision"] == "approve",
                actor=request.user,
                note=form.cleaned_data["note"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            outcome = "approved" if form.cleaned_data["decision"] == "approve" else "rejected"
            messages.success(request, f"Due-date change request {outcome}.")
        return redirect(reverse("fees:due_date_request_list"))

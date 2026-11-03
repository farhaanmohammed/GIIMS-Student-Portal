"""Reassignment request and approval screens."""

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.fees.forms import FeeReassignmentDecisionForm, FeeReassignmentForm
from apps.fees.models import FeePlan, FeePlanReassignment
from apps.fees.reassignment_services import (
    decide_fee_plan_reassignment,
    request_fee_plan_reassignment,
)
from apps.fees.selectors import current_fee_plan, pending_reassignments
from apps.fees.services import PLAN_APPROVER_ROLES, PLAN_EDITOR_ROLES
from apps.payments.models import InstallmentAllocation
from apps.payments.selectors import available_credit

PAGE_SIZE = 25


def _fee_plans_for_user(user) -> QuerySet[FeePlan]:
    return FeePlan.objects.filter(
        admission__centre__in=user.get_accessible_centres()
    ).select_related("admission__student", "admission__centre")


def _can_approve(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in PLAN_APPROVER_ROLES)
    )


def _can_request(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in PLAN_EDITOR_ROLES)
    )


def _has_financial_history(admission) -> bool:
    if admission.payments.filter(status="POSTED").exists():
        return True
    if InstallmentAllocation.objects.filter(
        installment__fee_plan__admission=admission,
        status=InstallmentAllocation.Status.ACTIVE,
    ).exists():
        return True
    return available_credit(admission) > 0


class RoleRequiredViewMixin:
    """Role gate placed after ``LoginRequiredMixin`` in the MRO."""

    allowed_roles: frozenset[str] | tuple[str, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from apps.core.permissions import role_required

        checker = role_required(*self.allowed_roles)
        return checker(super().dispatch)(request, *args, **kwargs)


class FeeReassignmentCreateView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "fees/reassignment_form.html"
    allowed_roles = PLAN_EDITOR_ROLES

    def _get_plan(self, request: HttpRequest):
        raw = request.POST.get("plan") or request.GET.get("plan")
        if not raw or not str(raw).isdigit():
            return None
        return get_object_or_404(_fee_plans_for_user(request.user), pk=int(raw))

    def _render(self, request, to_plan, form):
        admission = to_plan.admission
        from_plan = current_fee_plan(admission)
        return render(
            request,
            self.template_name,
            {
                "to_plan": to_plan,
                "from_plan": from_plan,
                "admission": admission,
                "form": form,
                "previous_total": from_plan.total_payable if from_plan else 0,
                "proposed_total": to_plan.total_payable,
                "difference": (to_plan.total_payable - from_plan.total_payable if from_plan else 0),
                "had_payments": _has_financial_history(admission),
                "credit_available": available_credit(admission),
                "from_installments": (
                    from_plan.installments.all().order_by("sequence") if from_plan else []
                ),
                "to_installments": to_plan.installments.all().order_by("sequence"),
                "can_approve": _can_approve(request.user),
            },
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        to_plan = self._get_plan(request)
        if to_plan is None:
            messages.error(request, "Select a draft fee plan to reassign.")
            return redirect("fees:fee_plan_list")
        if to_plan.status != FeePlan.Status.DRAFT:
            messages.error(request, "Only a draft plan can be proposed for reassignment.")
            return redirect("fees:fee_plan_detail", pk=to_plan.pk)
        return self._render(request, to_plan, FeeReassignmentForm())

    def post(self, request: HttpRequest) -> HttpResponse:
        to_plan = self._get_plan(request)
        if to_plan is None:
            messages.error(request, "Select a draft fee plan to reassign.")
            return redirect("fees:fee_plan_list")
        form = FeeReassignmentForm(request.POST)
        if not form.is_valid():
            return self._render(request, to_plan, form)

        try:
            request_fee_plan_reassignment(
                to_plan,
                reason=form.cleaned_data["reason"],
                actor=request.user,
                request_key=form.cleaned_data.get("request_key") or None,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self._render(request, to_plan, form)

        messages.success(
            request,
            "Reassignment requested. ADMIN or MANAGEMENT must approve it.",
        )
        return redirect("fees:reassignment_list")


class FeeReassignmentListView(LoginRequiredMixin, RoleRequiredViewMixin, ListView):
    template_name = "fees/reassignment_list.html"
    context_object_name = "reassignments"
    paginate_by = PAGE_SIZE
    allowed_roles = PLAN_EDITOR_ROLES

    def get_filters(self) -> dict[str, str]:
        status = self.request.GET.get("status", "")
        return {
            "status": status if status in dict(FeePlanReassignment.Status.choices) else "",
        }

    def get_queryset(self):
        queryset = pending_reassignments(self.request.user)
        filters = self.get_filters()
        if filters["status"]:
            queryset = queryset.filter(status=filters["status"])
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["filters"] = self.get_filters()
        context["status_options"] = FeePlanReassignment.Status.choices
        context["can_approve"] = _can_approve(self.request.user)
        context["can_request"] = _can_request(self.request.user)
        context["decision_form"] = FeeReassignmentDecisionForm()
        return context


class FeeReassignmentDecideView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    allowed_roles = PLAN_APPROVER_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from apps.fees.models import FeePlanReassignment as Model

        reassignment = get_object_or_404(
            Model.objects.filter(admission__centre__in=request.user.get_accessible_centres()),
            pk=pk,
        )
        form = FeeReassignmentDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose approve or reject before submitting.")
            return redirect("fees:reassignment_list")

        try:
            decide_fee_plan_reassignment(
                reassignment,
                approve=form.cleaned_data["decision"] == "approve",
                actor=request.user,
                note=form.cleaned_data["note"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            outcome = "approved" if form.cleaned_data["decision"] == "approve" else "rejected"
            messages.success(request, f"Fee-plan reassignment {outcome}.")
        return redirect("fees:reassignment_list")

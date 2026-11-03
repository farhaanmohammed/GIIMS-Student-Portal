from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from apps.admissions.models import Admission
from apps.core.permissions import role_required
from apps.fees.selectors import admission_fee_summary, current_fee_plan, installment_outstanding
from apps.payments.forms import (
    CreditApplicationForm,
    PaymentForm,
    PaymentReversalForm,
    RefundDecisionForm,
    RefundForm,
)
from apps.payments.models import Payment, PaymentMode, PaymentReversal, Refund
from apps.payments.selectors import (
    available_credit,
    credit_history,
    fifo_suggestions,
    payments_for_user,
    pending_refunds,
    pending_reversals,
)
from apps.payments.services import (
    apply_advance_credit,
    decide_payment_reversal,
    decide_refund,
    record_payment,
    request_payment_reversal,
    request_refund,
)

PAGE_SIZE = 25
PAYMENT_ROLES = ("ADMIN", "MANAGEMENT", "ACCOUNTS")
APPROVER_ROLES = ("ADMIN", "MANAGEMENT")
ZERO = Decimal("0.00")


def _can_record_payment(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in PAYMENT_ROLES)
    )


def _can_approve(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or getattr(user, "role", None) in APPROVER_ROLES)
    )


def _urlencode_filters(filters: dict[str, str]) -> str:
    return urlencode({key: value for key, value in filters.items() if value})


def _parse_amount(raw) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value > ZERO else None


def _get_admission_for_user(user, raw_id) -> Admission | None:
    if not raw_id or not str(raw_id).isdigit():
        return None
    return (
        Admission.objects.select_related("student", "centre", "course", "batch")
        .filter(pk=int(raw_id), centre__in=user.get_accessible_centres())
        .first()
    )


class RoleRequiredViewMixin:
    """Role gate placed after ``LoginRequiredMixin``."""

    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        checker = role_required(*self.allowed_roles)
        return checker(super().dispatch)(request, *args, **kwargs)


class PostOnlyMixin:
    """Return 405 for anything other than POST."""

    @method_decorator(require_POST)
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(request, *args, **kwargs)


class PaymentListView(LoginRequiredMixin, ListView):
    model = Payment
    template_name = "payments/payment_list.html"
    partial_template_name = "payments/partials/_payment_table.html"
    context_object_name = "payments"
    paginate_by = PAGE_SIZE

    def get_filters(self) -> dict[str, str]:
        cached = getattr(self, "_filters", None)
        if cached is not None:
            return cached
        centre = self.request.GET.get("centre", "")
        accessible_ids = {
            str(pk)
            for pk in self.request.user.get_accessible_centres().values_list("pk", flat=True)
        }
        mode = self.request.GET.get("mode", "")
        status = self.request.GET.get("status", "")
        filters = {
            "q": (self.request.GET.get("q") or "").strip()[:100],
            "mode": mode if mode in PaymentMode.values else "",
            "status": status if status in dict(Payment.Status.choices) else "",
            "centre": centre if centre in accessible_ids else "",
        }
        self._filters = filters
        return filters

    def get_queryset(self) -> QuerySet[Payment]:
        queryset = payments_for_user(self.request.user)
        filters = self.get_filters()
        if filters["q"]:
            queryset = queryset.filter(
                Q(admission__student__student_id__icontains=filters["q"])
                | Q(admission__student__first_name__icontains=filters["q"])
                | Q(admission__student__last_name__icontains=filters["q"])
                | Q(receipt__number__icontains=filters["q"])
                | Q(reference__icontains=filters["q"])
            )
        if filters["mode"]:
            queryset = queryset.filter(mode=filters["mode"])
        if filters["status"]:
            queryset = queryset.filter(status=filters["status"])
        if filters["centre"]:
            queryset = queryset.filter(centre_id=int(filters["centre"]))
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["filters"] = self.get_filters()
        context["querystring"] = _urlencode_filters(context["filters"])
        context["mode_options"] = PaymentMode.choices
        context["status_options"] = Payment.Status.choices
        context["centre_options"] = self.request.user.get_accessible_centres()
        context["can_record_payment"] = _can_record_payment(self.request.user)
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


class PaymentCreateView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "payments/payment_form.html"
    allowed_roles = PAYMENT_ROLES

    def _context(self, request, admission, form) -> dict[str, Any]:
        plan = current_fee_plan(admission)
        outstanding = ZERO
        if plan is not None:
            outstanding = sum(
                (installment_outstanding(row) for row in plan.installments.all()),
                ZERO,
            )
        amount = _parse_amount(request.GET.get("amount"))
        return {
            "admission": admission,
            "fee_plan": plan,
            "form": form,
            "summary": admission_fee_summary(admission),
            "credit_available": available_credit(admission),
            "suggestions": fifo_suggestions(admission, amount or outstanding),
            "suggested_for": amount or outstanding,
            "total_outstanding": outstanding,
            "prefill_allocations": amount is not None,
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(request.user, request.GET.get("admission"))
        if admission is None:
            messages.error(request, "Select an enrollment before recording a payment.")
            return redirect("payments:payment_list")
        plan = current_fee_plan(admission)
        if plan is None:
            messages.error(request, "An approved fee plan is required before collecting a payment.")
            return redirect("admissions:admission_detail", pk=admission.pk)

        amount = _parse_amount(request.GET.get("amount"))
        form = PaymentForm(
            admission=admission,
            user=request.user,
            suggestion_amount=amount,
            prefill_allocations=amount is not None,
        )
        return render(request, self.template_name, self._context(request, admission, form))

    def post(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(request.user, request.GET.get("admission"))
        if admission is None:
            messages.error(request, "Select an enrollment before recording a payment.")
            return redirect("payments:payment_list")

        form = PaymentForm(request.POST, admission=admission, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, self._context(request, admission, form))

        try:
            payment = record_payment(
                admission,
                amount=form.cleaned_data["amount"],
                paid_on=form.cleaned_data["paid_on"],
                mode=form.cleaned_data["mode"],
                collection_centre=form.cleaned_data["collection_centre"],
                reference=form.cleaned_data["reference"],
                notes=form.cleaned_data["notes"],
                allocations=form.allocations_argument(),
                request_key=form.cleaned_data.get("request_key") or uuid4().hex,
                actor=request.user,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, self._context(request, admission, form))

        messages.success(request, f"Payment recorded. Receipt {payment.receipt.number} issued.")
        return redirect("payments:payment_detail", pk=payment.pk)


class PaymentDetailView(LoginRequiredMixin, DetailView):
    model = Payment
    template_name = "payments/payment_detail.html"
    context_object_name = "payment"

    def get_queryset(self) -> QuerySet[Payment]:
        return payments_for_user(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        payment = self.object
        context.update(
            {
                "allocations": installment_allocations_for_payment(payment),
                "credit_lots": payment.credit_lots.select_related("source_payment").order_by(
                    "created_at", "pk"
                ),
                "refunds": payment.refunds.select_related("decided_by", "requested_by"),
                "reversal": PaymentReversal.objects.filter(payment=payment)
                .select_related("requested_by", "decided_by")
                .first(),
                "summary": admission_fee_summary(payment.admission),
                "credit_available": available_credit(payment.admission),
                "can_record_payment": _can_record_payment(self.request.user),
                "can_approve": _can_approve(self.request.user),
            }
        )
        return context


def installment_allocations_for_payment(payment: Payment):
    return payment.allocations.select_related(
        "installment", "credit", "credit__source_payment", "allocated_by", "reversed_by"
    ).order_by("installment__sequence", "allocated_at", "pk")


class CreditApplicationView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "payments/credit_form.html"
    allowed_roles = PAYMENT_ROLES

    def _context(self, admission, form) -> dict[str, Any]:
        return {
            "admission": admission,
            "form": form,
            "summary": admission_fee_summary(admission),
            "credit_available": available_credit(admission),
            "lots": credit_history(admission),
            "suggestions": fifo_suggestions(admission, available_credit(admission)),
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(request.user, request.GET.get("admission"))
        if admission is None:
            messages.error(request, "Select an enrollment before applying credit.")
            return redirect("payments:payment_list")
        form = CreditApplicationForm(admission=admission)
        return render(request, self.template_name, self._context(admission, form))

    def post(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(request.user, request.GET.get("admission"))
        if admission is None:
            messages.error(request, "Select an enrollment before applying credit.")
            return redirect("payments:payment_list")
        form = CreditApplicationForm(request.POST, admission=admission)
        if not form.is_valid():
            return render(request, self.template_name, self._context(admission, form))

        try:
            applied = apply_advance_credit(
                form.cleaned_data["installment"],
                amount=form.cleaned_data["amount"],
                actor=request.user,
                request_key=form.cleaned_data.get("request_key") or uuid4().hex,
                reason=form.cleaned_data.get("reason", ""),
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, self._context(admission, form))

        messages.success(request, f"Advance credit of {applied} applied to the installment.")
        return redirect("admissions:admission_detail", pk=admission.pk)


class RefundRequestView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "payments/refund_form.html"
    allowed_roles = PAYMENT_ROLES

    def _context(self, admission, form) -> dict[str, Any]:
        return {
            "admission": admission,
            "form": form,
            "summary": admission_fee_summary(admission),
            "credit_available": available_credit(admission),
            "lots": credit_history(admission),
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(
            request.user, request.POST.get("admission") or request.GET.get("admission")
        )
        if admission is None:
            messages.error(request, "Select an enrollment before requesting a refund.")
            return redirect("payments:payment_list")
        form = RefundForm(admission=admission)
        return render(request, self.template_name, self._context(admission, form))

    def post(self, request: HttpRequest) -> HttpResponse:
        admission = _get_admission_for_user(
            request.user, request.POST.get("admission") or request.GET.get("admission")
        )
        if admission is None:
            messages.error(request, "Select an enrollment before requesting a refund.")
            return redirect("payments:payment_list")
        form = RefundForm(request.POST, admission=admission)
        if not form.is_valid():
            return render(request, self.template_name, self._context(admission, form))

        try:
            request_refund(
                form.cleaned_data["credit"],
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                mode=form.cleaned_data["mode"],
                actor=request.user,
                request_key=form.cleaned_data.get("request_key") or uuid4().hex,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return render(request, self.template_name, self._context(admission, form))

        messages.success(request, "Refund request submitted for approval.")
        return redirect("payments:payment_list")


class RefundRequestListView(LoginRequiredMixin, RoleRequiredViewMixin, ListView):
    template_name = "payments/refund_list.html"
    context_object_name = "refunds"
    paginate_by = PAGE_SIZE
    allowed_roles = APPROVER_ROLES

    def get_queryset(self):
        return pending_refunds(self.request.user)


class RefundDecideView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = APPROVER_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        refund = get_object_or_404(
            Refund.objects.filter(admission__centre__in=request.user.get_accessible_centres()),
            pk=pk,
        )
        form = RefundDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose approve or reject before submitting.")
            return redirect("payments:refund_list")

        try:
            decide_refund(
                refund,
                approve=form.cleaned_data["decision"] == "approve",
                actor=request.user,
                note=form.cleaned_data["note"],
                refunded_on=form.cleaned_data.get("refunded_on"),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            outcome = "approved" if form.cleaned_data["decision"] == "approve" else "rejected"
            messages.success(request, f"Refund {outcome}.")
        return redirect("payments:refund_list")


class ReversalRequestView(LoginRequiredMixin, RoleRequiredViewMixin, View):
    template_name = "payments/reversal_form.html"
    allowed_roles = PAYMENT_ROLES

    def _get_payment(self, request, pk: int) -> Payment:
        return get_object_or_404(payments_for_user(request.user), pk=pk)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        payment = self._get_payment(request, pk)
        return render(
            request,
            self.template_name,
            {
                "payment": payment,
                "form": PaymentReversalForm(),
                "summary": admission_fee_summary(payment.admission),
                "existing_reversal": PaymentReversal.objects.filter(payment=payment).first(),
            },
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        payment = self._get_payment(request, pk)
        form = PaymentReversalForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "payment": payment,
                    "form": form,
                    "summary": admission_fee_summary(payment.admission),
                    "existing_reversal": PaymentReversal.objects.filter(payment=payment).first(),
                },
            )
        try:
            request_payment_reversal(
                payment,
                reason=form.cleaned_data["reason"],
                actor=request.user,
                request_key=form.cleaned_data.get("request_key") or uuid4().hex,
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return render(
                request,
                self.template_name,
                {
                    "payment": payment,
                    "form": form,
                    "summary": admission_fee_summary(payment.admission),
                    "existing_reversal": PaymentReversal.objects.filter(payment=payment).first(),
                },
            )
        messages.success(request, "Reversal request submitted for approval.")
        return redirect("payments:payment_detail", pk=payment.pk)


class ReversalRequestListView(LoginRequiredMixin, RoleRequiredViewMixin, ListView):
    template_name = "payments/reversal_list.html"
    context_object_name = "reversals"
    paginate_by = PAGE_SIZE
    allowed_roles = APPROVER_ROLES

    def get_queryset(self):
        return pending_reversals(self.request.user)


class ReversalDecideView(LoginRequiredMixin, RoleRequiredViewMixin, PostOnlyMixin, View):
    allowed_roles = APPROVER_ROLES

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        reversal = get_object_or_404(
            PaymentReversal.objects.filter(
                payment__admission__centre__in=request.user.get_accessible_centres()
            ),
            pk=pk,
        )
        from apps.payments.forms import DecisionForm

        form = DecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose approve or reject before submitting.")
            return redirect("payments:reversal_list")

        try:
            decide_payment_reversal(
                reversal,
                approve=form.cleaned_data["decision"] == "approve",
                actor=request.user,
                note=form.cleaned_data["note"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            outcome = "approved" if form.cleaned_data["decision"] == "approve" else "rejected"
            messages.success(request, f"Payment reversal {outcome}.")
        return redirect("payments:reversal_list")

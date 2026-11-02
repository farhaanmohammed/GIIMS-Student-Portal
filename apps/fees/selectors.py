"""
Read-only fee selectors.

Overdue is derived here, never stored and never enforced. An installment is
overdue when it has a positive outstanding balance and its *effective* due
date is strictly before the requested as-of date. Stage-based application and
admission charges have no date and are therefore never overdue.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.fees.models import DueDateChangeRequest, FeeInstallment, FeePlan

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class FeeSummary:
    """As-of snapshot of an admission's fee position."""

    as_of: date
    total_billed: Decimal
    total_paid: Decimal
    total_outstanding: Decimal
    overdue_outstanding: Decimal
    not_yet_due_outstanding: Decimal
    undated_outstanding: Decimal


def effective_due_date(installment: FeeInstallment) -> date | None:
    """Latest applied revision date, else the original suggested date."""
    if installment.category != FeeInstallment.Category.COURSE:
        return None
    latest = installment.due_date_revisions.order_by("-created_at", "-pk").first()
    if latest is not None:
        return latest.revised_due_date
    return installment.original_due_date


def current_fee_plan(admission) -> FeePlan | None:
    """The single approved plan for an admission, if any."""
    return admission.fee_plans.filter(status=FeePlan.Status.APPROVED).first()


def plan_history(admission):
    """Every plan version for an admission, oldest first."""
    return admission.fee_plans.order_by("version")


def installment_outstanding(installment: FeeInstallment, as_of: date | None = None) -> Decimal:
    """
    Amount billed minus posted allocations.

    Payment allocations arrive in Phase 5. Until then nothing has been
    allocated, so the outstanding balance equals the approved amount. The
    ``as_of`` parameter is accepted so callers already pass a reporting date,
    and so the signature does not change when allocations are added.
    """
    del as_of  # Allocation history is introduced in Phase 5.
    return installment.amount


def admission_fee_summary(admission, as_of: date | None = None) -> FeeSummary:
    """Derive billed, paid, outstanding, overdue, and undated totals."""
    as_of = as_of or timezone.localdate()
    plan = current_fee_plan(admission)
    if plan is None:
        return FeeSummary(
            as_of=as_of,
            total_billed=ZERO,
            total_paid=ZERO,
            total_outstanding=ZERO,
            overdue_outstanding=ZERO,
            not_yet_due_outstanding=ZERO,
            undated_outstanding=ZERO,
        )

    total_billed = ZERO
    total_outstanding = ZERO
    overdue_outstanding = ZERO
    not_yet_due_outstanding = ZERO
    undated_outstanding = ZERO

    for installment in plan.installments.all():
        outstanding = installment_outstanding(installment, as_of)
        total_billed += installment.amount
        total_outstanding += outstanding
        if outstanding <= ZERO:
            continue

        due_date = effective_due_date(installment)
        if due_date is None:
            undated_outstanding += outstanding
        elif due_date < as_of:
            overdue_outstanding += outstanding
        else:
            not_yet_due_outstanding += outstanding

    return FeeSummary(
        as_of=as_of,
        total_billed=total_billed,
        total_paid=total_billed - total_outstanding,
        total_outstanding=total_outstanding,
        overdue_outstanding=overdue_outstanding,
        not_yet_due_outstanding=not_yet_due_outstanding,
        undated_outstanding=undated_outstanding,
    )


def pending_due_date_requests(user, centre=None):
    """Pending requests visible to ``user``, optionally narrowed to one centre."""
    queryset = (
        DueDateChangeRequest.objects.filter(
            status=DueDateChangeRequest.Status.PENDING,
            installment__fee_plan__status=FeePlan.Status.APPROVED,
            installment__fee_plan__admission__centre__in=user.get_accessible_centres(),
        )
        .select_related(
            "installment",
            "installment__fee_plan",
            "installment__fee_plan__admission__student",
            "installment__fee_plan__admission__centre",
            "requested_by",
        )
        .order_by("requested_at", "pk")
    )
    if centre is not None:
        queryset = queryset.filter(installment__fee_plan__admission__centre=centre)
    return queryset

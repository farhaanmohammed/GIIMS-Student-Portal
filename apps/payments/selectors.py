"""
Read-only payment, allocation, credit, refund, and reversal selectors.

Balances are always derived from rows: an installment's outstanding balance is
its approved amount minus active allocations. Credit balances come from the
locked credit-lot rows; ``credit_remaining_from_history`` recomputes the same
figure from allocation and refund history for reconciliation tests.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from apps.fees.models import FeeInstallment, FeePlan
from apps.payments.models import (
    AdvanceCredit,
    InstallmentAllocation,
    Payment,
    PaymentReversal,
    Refund,
)

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class FifoSuggestion:
    """One suggested allocation row for the payment form."""

    installment: FeeInstallment
    outstanding: Decimal
    suggested_amount: Decimal


def active_allocations():
    return InstallmentAllocation.objects.filter(status=InstallmentAllocation.Status.ACTIVE)


def installment_allocated_total(installment: FeeInstallment) -> Decimal:
    """Sum of active allocations (payment and credit) against one installment."""
    total = (
        active_allocations().filter(installment=installment).aggregate(total=Sum("amount"))["total"]
    )
    return total or ZERO


def installment_outstanding(installment: FeeInstallment) -> Decimal:
    """Approved amount minus active allocations. Never negative."""
    outstanding = installment.amount - installment_allocated_total(installment)
    return outstanding if outstanding > ZERO else ZERO


def installment_allocations(installment: FeeInstallment):
    return (
        InstallmentAllocation.objects.filter(installment=installment)
        .select_related("payment", "credit", "credit__source_payment", "allocated_by")
        .order_by("allocated_at", "pk")
    )


def available_credit(admission) -> Decimal:
    """Total unapplied, unrefunded advance credit for an admission."""
    total = AdvanceCredit.objects.filter(
        admission=admission,
        status=AdvanceCredit.Status.ACTIVE,
    ).aggregate(total=Sum("remaining_amount"))["total"]
    return total or ZERO


def credit_remaining_from_history(credit: AdvanceCredit) -> Decimal:
    """
    Recompute a lot's remaining amount from its movements.

    Equals ``original_amount`` minus active credit allocations minus approved
    refund allocations. Used to verify the stored balance stays consistent.

    A reversed lot has no remaining value by definition: its applications have
    been reversed and the lot is zeroed, so the derived figure is also zero.
    """
    if credit.status == AdvanceCredit.Status.REVERSED:
        return ZERO
    applied = (
        InstallmentAllocation.objects.filter(
            credit=credit,
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
            status=InstallmentAllocation.Status.ACTIVE,
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    refunded = credit.refund_allocations.aggregate(total=Sum("amount"))["total"] or ZERO
    return credit.original_amount - applied - refunded


def fifo_suggestions(admission, amount) -> list[FifoSuggestion]:
    """
    Oldest-first allocation suggestion for a proposed payment amount.

    Order is the plan's sequence: application, admission, then the four dated
    course installments. Suggestions never exceed an installment's outstanding
    balance and the unallocated remainder becomes advance credit.
    """
    amount = Decimal(str(amount or 0))
    plan = (
        FeePlan.objects.filter(admission=admission, status=FeePlan.Status.APPROVED)
        .prefetch_related("installments")
        .first()
    )
    if plan is None:
        return []

    rows: list[FifoSuggestion] = []
    remaining = amount
    for installment in plan.installments.all():
        outstanding = installment_outstanding(installment)
        suggested = min(outstanding, remaining) if outstanding > ZERO and remaining > ZERO else ZERO
        rows.append(
            FifoSuggestion(
                installment=installment,
                outstanding=outstanding,
                suggested_amount=suggested,
            )
        )
        remaining -= suggested
    return rows


def payments_for_user(user):
    return (
        Payment.objects.filter(admission__centre__in=user.get_accessible_centres())
        .select_related(
            "admission__student",
            "admission__centre",
            "centre",
            "recorded_by",
            "receipt",
        )
        .order_by("-paid_on", "-posted_at", "-pk")
    )


def credit_lots_for_user(user):
    return (
        AdvanceCredit.objects.filter(admission__centre__in=user.get_accessible_centres())
        .select_related("admission__student", "admission__centre", "source_payment")
        .order_by("created_at", "pk")
    )


def admission_payment_total(admission) -> Decimal:
    total = Payment.objects.filter(admission=admission, status=Payment.Status.POSTED).aggregate(
        total=Sum("amount")
    )["total"]
    return total or ZERO


def pending_refunds(user, centre=None):
    queryset = (
        Refund.objects.filter(
            status=Refund.Status.PENDING,
            admission__centre__in=user.get_accessible_centres(),
        )
        .select_related(
            "admission__student",
            "admission__centre",
            "credit",
            "credit__source_payment",
            "original_payment",
            "requested_by",
        )
        .order_by("requested_at", "pk")
    )
    if centre is not None:
        queryset = queryset.filter(admission__centre=centre)
    return queryset


def pending_reversals(user, centre=None):
    queryset = (
        PaymentReversal.objects.filter(
            status=PaymentReversal.Status.PENDING,
            payment__admission__centre__in=user.get_accessible_centres(),
        )
        .select_related(
            "payment",
            "payment__admission__student",
            "payment__admission__centre",
            "payment__centre",
            "requested_by",
        )
        .order_by("requested_at", "pk")
    )
    if centre is not None:
        queryset = queryset.filter(payment__admission__centre=centre)
    return queryset


def credit_history(admission):
    """Credit lots first, each with its applications and refunds (oldest first)."""
    lots = list(
        AdvanceCredit.objects.filter(admission=admission)
        .select_related("source_payment", "created_by")
        .prefetch_related(
            "allocations__installment",
            "allocations__allocated_by",
            "refund_allocations__refund",
        )
        .order_by("created_at", "pk")
    )
    return lots

"""
Controlled fee-plan reassignment (Phase 5, decision A4).

``ACCOUNTS`` may raise a reassignment request. Only ``ADMIN`` and
``MANAGEMENT`` may approve it. Approval runs a reconciliation:

1. every active allocation on the old plan is reversed (never deleted);
2. credit-funded allocations return their amount to the source credit lot;
3. payment-funded allocations are preserved as a new advance-credit lot;
4. the new plan version is approved (the old one becomes ``SUPERSEDED``);
5. available credit is applied to the new installments oldest-first.

Payments and receipts are never edited or deleted, and no installment amount
is rewritten.
"""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.admissions.models import Admission
from apps.fees.models import FeePlan, FeePlanReassignment
from apps.fees.services import PLAN_APPROVER_ROLES, PLAN_EDITOR_ROLES, approve_fee_plan
from apps.payments.models import AdvanceCredit, FinancialAuditEvent, InstallmentAllocation
from apps.payments.selectors import available_credit
from apps.payments.services import ZERO, apply_credit_fifo

MIN_REASON_LENGTH = 10


def _is_authenticated(actor) -> bool:
    return bool(actor is not None and getattr(actor, "is_authenticated", False))


def _require_role(actor, roles: frozenset[str], action: str) -> None:
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")
    if not (actor.is_superuser or getattr(actor, "role", None) in roles):
        raise PermissionDenied(f"You do not have permission to {action}.")


def _require_centre_access(actor, admission: Admission) -> None:
    if not actor.can_access_centre(admission.centre_id):
        raise PermissionDenied("You do not have access to this admission's centre.")


def _validate_reason(reason) -> str:
    cleaned = (reason or "").strip()
    if len(cleaned) < MIN_REASON_LENGTH:
        raise ValueError(f"A reason of at least {MIN_REASON_LENGTH} characters is required.")
    return cleaned


def _audit(reassignment: FeePlanReassignment, event_type: str, actor, detail: str = ""):
    return FinancialAuditEvent.objects.create(
        admission=reassignment.admission,
        event_type=event_type,
        actor=actor,
        amount=abs(reassignment.difference),
        detail=detail,
    )


def _admission_has_financial_history(admission: Admission) -> bool:
    if admission.payments.filter(status="POSTED").exists():
        return True
    if InstallmentAllocation.objects.filter(
        installment__fee_plan__admission=admission,
        status=InstallmentAllocation.Status.ACTIVE,
    ).exists():
        return True
    return available_credit(admission) > ZERO


@transaction.atomic
def request_fee_plan_reassignment(
    to_plan: FeePlan,
    *,
    reason,
    actor,
    request_key: str | None = None,
) -> FeePlanReassignment:
    """Raise a reassignment request from the current approved plan to a draft."""
    _require_role(actor, PLAN_EDITOR_ROLES, "request fee-plan reassignments")
    reason = _validate_reason(reason)

    target = FeePlan.objects.select_related("admission__centre").get(pk=to_plan.pk)
    admission = (
        Admission.objects.select_for_update().select_related("centre").get(pk=target.admission_id)
    )
    _require_centre_access(actor, admission)

    if request_key:
        existing = FeePlanReassignment.objects.filter(request_key=request_key).first()
        if existing is not None:
            if existing.admission_id != admission.pk or existing.to_plan_id != target.pk:
                raise ValueError("This idempotency key belongs to another reassignment request.")
            return existing

    if target.status != FeePlan.Status.DRAFT:
        raise ValueError("The proposed plan must be an unapproved draft.")

    from_plan = (
        FeePlan.objects.select_for_update()
        .filter(admission=admission, status=FeePlan.Status.APPROVED)
        .first()
    )
    if from_plan is None:
        raise ValueError("This admission has no approved fee plan to reassign from.")
    if target.version <= from_plan.version:
        raise ValueError("Create a new fee-plan version before requesting a reassignment.")

    to_plan = FeePlan.objects.select_for_update().get(pk=target.pk)
    if FeePlanReassignment.objects.filter(
        admission=admission, status=FeePlanReassignment.Status.PENDING
    ).exists():
        raise ValueError("A reassignment request is already pending for this admission.")

    difference = to_plan.total_payable - from_plan.total_payable
    reassignment = FeePlanReassignment.objects.create(
        admission=admission,
        from_plan=from_plan,
        to_plan=to_plan,
        reason=reason,
        previous_total=from_plan.total_payable,
        proposed_total=to_plan.total_payable,
        difference=difference,
        had_payments=_admission_has_financial_history(admission),
        requested_by=actor,
        request_key=request_key or None,
    )
    _audit(
        reassignment,
        FinancialAuditEvent.Event.REASSIGNMENT_REQUESTED,
        actor,
        f"Reassignment {reassignment.pk} requested; difference {difference}.",
    )
    return reassignment


@transaction.atomic
def decide_fee_plan_reassignment(
    reassignment: FeePlanReassignment,
    *,
    approve: bool,
    actor,
    note: str = "",
) -> FeePlanReassignment:
    """Approve (reconciling all money) or reject a reassignment request."""
    _require_role(actor, PLAN_APPROVER_ROLES, "decide fee-plan reassignments")

    target = FeePlanReassignment.objects.select_related("admission__centre").get(pk=reassignment.pk)
    admission = (
        Admission.objects.select_for_update().select_related("centre").get(pk=target.admission_id)
    )
    _require_centre_access(actor, admission)
    reassignment = FeePlanReassignment.objects.select_for_update().get(pk=target.pk)
    if reassignment.status != FeePlanReassignment.Status.PENDING:
        raise ValueError("Only pending reassignment requests can be decided.")

    if approve:
        from_plan = FeePlan.objects.select_for_update().get(pk=reassignment.from_plan_id)
        to_plan = FeePlan.objects.select_for_update().get(pk=reassignment.to_plan_id)
        if from_plan.status != FeePlan.Status.APPROVED:
            raise ValueError("The original plan is no longer the approved plan.")
        if to_plan.status != FeePlan.Status.DRAFT:
            raise ValueError("The proposed plan is no longer a draft.")

        reason = f"Fee-plan reassignment {reassignment.pk}: {reassignment.reason}"
        released_payment_total = ZERO
        allocations = InstallmentAllocation.objects.select_for_update().filter(
            installment__fee_plan=from_plan,
            status=InstallmentAllocation.Status.ACTIVE,
        )
        for allocation in allocations:
            allocation.status = InstallmentAllocation.Status.REVERSED
            allocation.reversed_by = actor
            allocation.reversed_at = timezone.now()
            allocation.reversal_reason = reason
            allocation.save(
                update_fields=[
                    "status",
                    "reversed_by",
                    "reversed_at",
                    "reversal_reason",
                    "updated_at",
                ]
            )
            if allocation.source_type == InstallmentAllocation.Source.PAYMENT:
                released_payment_total += allocation.amount
            else:
                credit = AdvanceCredit.objects.select_for_update().get(pk=allocation.credit_id)
                if credit.status != AdvanceCredit.Status.REVERSED:
                    credit.remaining_amount += allocation.amount
                    credit.status = AdvanceCredit.Status.ACTIVE
                    credit.save(update_fields=["remaining_amount", "status", "updated_at"])

        if released_payment_total > ZERO:
            AdvanceCredit.objects.create(
                admission=admission,
                source_payment=None,
                source_type=AdvanceCredit.Source.REASSIGNMENT,
                original_amount=released_payment_total,
                remaining_amount=released_payment_total,
                created_by=actor,
                notes=reason,
            )

        approve_fee_plan(to_plan, actor)
        applied = apply_credit_fifo(
            admission,
            actor=actor,
            request_key_prefix=f"reassign:{reassignment.pk}",
            plan=to_plan,
        )

        reassignment.status = FeePlanReassignment.Status.APPROVED
        _audit(
            reassignment,
            FinancialAuditEvent.Event.REASSIGNMENT_APPROVED,
            actor,
            (
                f"Reassignment {reassignment.pk} approved; plan {from_plan.pk} -> "
                f"{to_plan.pk}; credit applied {applied}; note: {note}".strip()
            ),
        )
    else:
        reassignment.status = FeePlanReassignment.Status.REJECTED
        _audit(
            reassignment,
            FinancialAuditEvent.Event.REASSIGNMENT_REJECTED,
            actor,
            f"Reassignment {reassignment.pk} rejected. {note}".strip(),
        )

    reassignment.decided_by = actor
    reassignment.decided_at = timezone.now()
    reassignment.decision_note = (note or "").strip()
    reassignment.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return reassignment

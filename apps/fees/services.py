"""
Fee plan lifecycle and due-date workflow services.

Every function here is atomic, re-validates server-side, and never trusts
client-submitted totals. No service computes or applies a late fee, penalty,
discount, waiver, or concession.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.admissions.models import Admission
from apps.fees.models import (
    DueDateChangeRequest,
    FeeInstallment,
    FeePlan,
    InstallmentDueDateRevision,
)
from apps.fees.selectors import effective_due_date

PLAN_MANAGER_ROLES = frozenset({"ADMIN", "MANAGEMENT"})
DUE_DATE_APPLIER_ROLES = frozenset({"ADMIN", "MANAGEMENT", "ACCOUNTS"})
MIN_REASON_LENGTH = 10
MONEY_QUANTUM = Decimal("0.01")
GST_RATE_QUANTUM = Decimal("0.01")
PERCENT = Decimal("100")
EXPECTED_CATEGORY_COUNTS = Counter({"APPLICATION": 1, "ADMISSION": 1, "COURSE": 4})
COMPONENT_COUNT = 6


@dataclass(frozen=True)
class FeeComponent:
    """A single validated component ready to be persisted."""

    sequence: int
    category: str
    description: str
    amount: Decimal
    original_due_date: date | None = None


def _is_authenticated(actor) -> bool:
    return bool(actor is not None and getattr(actor, "is_authenticated", False))


def _has_role(actor, roles: frozenset[str]) -> bool:
    return bool(actor.is_superuser or getattr(actor, "role", None) in roles)


def _require_role(actor, roles: frozenset[str], action: str) -> None:
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")
    if not _has_role(actor, roles):
        raise PermissionDenied(f"You do not have permission to {action}.")


def _require_centre_access(actor, admission: Admission) -> None:
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")
    if not actor.can_access_centre(admission.centre_id):
        raise PermissionDenied("You do not have access to this admission's centre.")


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Enter a valid monetary amount.") from exc


def compute_gst_amounts(course_fee, gst_rate) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Return ``(course_fee, gst_rate, gst_amount, total_payable)``.

    The GST rate is stored per plan. ``gst_amount`` is derived here so a client
    can never submit a total. Rounding is half-up to two decimal places.
    """
    taxable = _money(course_fee)
    rate = _money(gst_rate)
    if taxable < 0:
        raise ValueError("Course fee cannot be negative.")
    if rate < 0 or rate > PERCENT:
        raise ValueError("GST rate must be between 0 and 100.")
    gst_amount = _money(taxable * rate / PERCENT)
    total_payable = _money(taxable + gst_amount)
    return taxable, rate, gst_amount, total_payable


def normalize_components(components) -> list[FeeComponent]:
    """Coerce component mappings or dataclasses into ``FeeComponent`` rows."""
    rows: list[FeeComponent] = []
    for item in components:
        if isinstance(item, FeeComponent):
            rows.append(item)
            continue
        try:
            rows.append(
                FeeComponent(
                    sequence=int(item["sequence"]),
                    category=str(item["category"]),
                    description=str(item.get("description") or "").strip(),
                    amount=_money(item["amount"]),
                    original_due_date=item.get("original_due_date"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each fee component needs a sequence, category and amount.") from exc
    return rows


def validate_components(rows: list[FeeComponent], total_payable) -> None:
    """
    Enforce the D5 structure and that components are inside the approved total.

    Rules: exactly one application, one admission, and four course components;
    unique contiguous sequences from 1; positive amounts; stage-based charges
    without a calendar date; course components with a suggested date; and the
    component sum equal to ``total_payable``.
    """
    if len(rows) != COMPONENT_COUNT:
        raise ValueError("A fee plan must have exactly six components.")

    counts = Counter(row.category for row in rows)
    if counts != EXPECTED_CATEGORY_COUNTS:
        raise ValueError(
            "A fee plan needs exactly one application, one admission, and four course components."
        )

    if sorted(row.sequence for row in rows) != list(range(1, COMPONENT_COUNT + 1)):
        raise ValueError("Component sequences must be unique and contiguous from 1.")

    for row in rows:
        if not row.description:
            raise ValueError("Every fee component needs a description.")
        if row.amount <= 0:
            raise ValueError("Every fee component amount must be greater than zero.")
        if row.category == "COURSE" and row.original_due_date is None:
            raise ValueError("Every course installment needs a suggested due date.")
        if row.category in {"APPLICATION", "ADMISSION"} and row.original_due_date is not None:
            raise ValueError(
                "Application and admission charges are stage-based and must not have a due date."
            )

    component_total = sum((row.amount for row in rows), Decimal("0.00"))
    if component_total != _money(total_payable):
        raise ValueError(
            "Component amounts must add up to the plan total of "
            f"{_money(total_payable)} (got {component_total})."
        )


def _installment_rows(plan: FeePlan) -> list[FeeComponent]:
    return [
        FeeComponent(
            sequence=installment.sequence,
            category=installment.category,
            description=installment.description,
            amount=installment.amount,
            original_due_date=installment.original_due_date,
        )
        for installment in plan.installments.all()
    ]


def _create_installments(plan: FeePlan, rows: list[FeeComponent]) -> None:
    FeeInstallment.objects.bulk_create(
        [
            FeeInstallment(
                fee_plan=plan,
                sequence=row.sequence,
                category=row.category,
                description=row.description,
                amount=row.amount,
                original_due_date=row.original_due_date,
            )
            for row in rows
        ]
    )


def _next_version(admission: Admission) -> int:
    highest = FeePlan.objects.filter(admission=admission).aggregate(Max("version"))["version__max"]
    return (highest or 0) + 1


def _validate_reason(reason) -> str:
    cleaned = (reason or "").strip()
    if len(cleaned) < MIN_REASON_LENGTH:
        raise ValueError(f"A reason of at least {MIN_REASON_LENGTH} characters is required.")
    return cleaned


def _require_course_installment_on_approved_plan(installment: FeeInstallment) -> None:
    if installment.fee_plan.status != FeePlan.Status.APPROVED:
        raise ValueError("Due dates can only be revised on an approved fee plan.")
    if installment.category != FeeInstallment.Category.COURSE:
        raise ValueError(
            "Application and admission charges are stage-based and have no due date to revise."
        )


@transaction.atomic
def create_draft_fee_plan(
    admission: Admission,
    *,
    course_fee,
    gst_rate,
    components,
    actor,
    notes: str = "",
) -> FeePlan:
    """Create a first draft fee plan for an enrolled admission."""
    _require_role(actor, PLAN_MANAGER_ROLES, "create fee plans")

    admission = Admission.objects.select_related("centre").get(pk=admission.pk)
    _require_centre_access(actor, admission)
    if admission.status != Admission.Status.ENROLLED:
        raise ValueError("A fee plan can only be created for an enrolled admission.")
    if FeePlan.objects.filter(admission=admission, status=FeePlan.Status.APPROVED).exists():
        raise ValueError(
            "This admission already has an approved fee plan. Create a new version instead."
        )
    if FeePlan.objects.filter(admission=admission, status=FeePlan.Status.DRAFT).exists():
        raise ValueError("A draft fee plan already exists for this admission.")

    taxable, rate, gst_amount, total_payable = compute_gst_amounts(course_fee, gst_rate)
    rows = normalize_components(components)
    validate_components(rows, total_payable)

    plan = FeePlan.objects.create(
        admission=admission,
        version=_next_version(admission),
        status=FeePlan.Status.DRAFT,
        course_fee=taxable,
        gst_rate=rate,
        gst_amount=gst_amount,
        total_payable=total_payable,
        notes=notes or "",
    )
    _create_installments(plan, rows)
    return plan


@transaction.atomic
def update_draft_fee_plan(
    plan: FeePlan,
    *,
    course_fee=None,
    gst_rate=None,
    components=None,
    notes: str | None = None,
    actor,
) -> FeePlan:
    """Edit a draft plan. Approved, superseded, and cancelled plans are rejected."""
    _require_role(actor, PLAN_MANAGER_ROLES, "edit fee plans")

    plan = FeePlan.objects.select_for_update().select_related("admission__centre").get(pk=plan.pk)
    _require_centre_access(actor, plan.admission)
    if plan.status != FeePlan.Status.DRAFT:
        raise ValueError("Only draft fee plans can be edited.")

    taxable, rate, gst_amount, total_payable = compute_gst_amounts(
        plan.course_fee if course_fee is None else course_fee,
        plan.gst_rate if gst_rate is None else gst_rate,
    )

    if components is None:
        rows = _installment_rows(plan)
    else:
        has_due_date_history = (
            plan.installments.filter(due_date_revisions__isnull=False).exists()
            or plan.installments.filter(due_date_change_requests__isnull=False).exists()
        )
        if has_due_date_history:
            raise ValueError(
                "Cannot replace components on a plan that already has due-date history."
            )
        rows = normalize_components(components)

    validate_components(rows, total_payable)

    plan.course_fee = taxable
    plan.gst_rate = rate
    plan.gst_amount = gst_amount
    plan.total_payable = total_payable
    if notes is not None:
        plan.notes = notes
    plan.save(
        update_fields=[
            "course_fee",
            "gst_rate",
            "gst_amount",
            "total_payable",
            "notes",
            "updated_at",
        ]
    )

    if components is not None:
        plan.installments.all().delete()
        _create_installments(plan, rows)
    return plan


@transaction.atomic
def approve_fee_plan(plan: FeePlan, actor) -> FeePlan:
    """
    Approve a draft plan and supersede any previously approved version.

    Exactly one approved plan remains per admission. The previous version's
    installments and revision history are left untouched.
    """
    _require_role(actor, PLAN_MANAGER_ROLES, "approve fee plans")

    plan = FeePlan.objects.select_for_update().select_related("admission__centre").get(pk=plan.pk)
    _require_centre_access(actor, plan.admission)
    if plan.status != FeePlan.Status.DRAFT:
        raise ValueError("Only draft fee plans can be approved.")

    validate_components(_installment_rows(plan), plan.total_payable)

    now = timezone.now()
    FeePlan.objects.filter(admission=plan.admission, status=FeePlan.Status.APPROVED).exclude(
        pk=plan.pk
    ).update(status=FeePlan.Status.SUPERSEDED, updated_at=now)

    plan.status = FeePlan.Status.APPROVED
    plan.approved_at = now
    plan.approved_by = actor
    plan.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])
    return plan


@transaction.atomic
def create_fee_plan_version(plan: FeePlan, actor) -> FeePlan:
    """Copy an approved plan into a new draft version without touching the original."""
    _require_role(actor, PLAN_MANAGER_ROLES, "create fee plan versions")

    source = FeePlan.objects.select_for_update().select_related("admission__centre").get(pk=plan.pk)
    _require_centre_access(actor, source.admission)
    if source.status != FeePlan.Status.APPROVED:
        raise ValueError("Only an approved fee plan can be versioned.")
    if FeePlan.objects.filter(admission=source.admission, status=FeePlan.Status.DRAFT).exists():
        raise ValueError("A draft version already exists for this admission.")

    new_plan = FeePlan.objects.create(
        admission=source.admission,
        version=_next_version(source.admission),
        status=FeePlan.Status.DRAFT,
        supersedes=source,
        currency=source.currency,
        course_fee=source.course_fee,
        gst_rate=source.gst_rate,
        gst_amount=source.gst_amount,
        total_payable=source.total_payable,
        notes=source.notes,
    )
    _create_installments(new_plan, _installment_rows(source))
    return new_plan


@transaction.atomic
def cancel_draft_fee_plan(plan: FeePlan, actor, reason) -> FeePlan:
    """Abandon a draft plan. Cancelling an approved plan is out of scope."""
    _require_role(actor, PLAN_MANAGER_ROLES, "cancel fee plans")
    reason = _validate_reason(reason)

    plan = FeePlan.objects.select_for_update().select_related("admission__centre").get(pk=plan.pk)
    _require_centre_access(actor, plan.admission)
    if plan.status != FeePlan.Status.DRAFT:
        raise ValueError("Only draft fee plans can be cancelled.")

    cancellation_note = f"Cancelled: {reason}"
    plan.notes = f"{plan.notes}\n\n{cancellation_note}".strip() if plan.notes else cancellation_note
    plan.status = FeePlan.Status.CANCELLED
    plan.save(update_fields=["status", "notes", "updated_at"])
    return plan


@transaction.atomic
def request_due_date_change(
    installment: FeeInstallment,
    *,
    proposed_due_date,
    reason,
    actor,
) -> DueDateChangeRequest:
    """
    Raise a pending request. Any centre-authorized role may do this, including
    ``ADMISSIONS``, which cannot apply the change itself.
    """
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")

    installment = (
        FeeInstallment.objects.select_for_update()
        .select_related("fee_plan__admission__centre")
        .get(pk=installment.pk)
    )
    _require_centre_access(actor, installment.fee_plan.admission)
    _require_course_installment_on_approved_plan(installment)
    reason = _validate_reason(reason)
    if proposed_due_date is None:
        raise ValueError("A proposed due date is required.")
    if installment.due_date_change_requests.filter(
        status=DueDateChangeRequest.Status.PENDING
    ).exists():
        raise ValueError("A pending due-date change request already exists for this installment.")

    current_due_date = effective_due_date(installment)
    if proposed_due_date == current_due_date:
        raise ValueError("The proposed due date matches the current effective due date.")

    return DueDateChangeRequest.objects.create(
        installment=installment,
        requested_by=actor,
        previous_due_date=current_due_date,
        proposed_due_date=proposed_due_date,
        reason=reason,
    )


@transaction.atomic
def decide_due_date_change(
    request: DueDateChangeRequest,
    *,
    approve: bool,
    actor,
    note: str = "",
) -> DueDateChangeRequest:
    """Approve (creating the revision) or reject a pending request."""
    _require_role(actor, DUE_DATE_APPLIER_ROLES, "decide due-date change requests")

    request = (
        DueDateChangeRequest.objects.select_for_update()
        .select_related("installment__fee_plan__admission__centre")
        .get(pk=request.pk)
    )
    _require_centre_access(actor, request.installment.fee_plan.admission)
    if request.status != DueDateChangeRequest.Status.PENDING:
        raise ValueError("Only pending requests can be decided.")

    installment = request.installment
    _require_course_installment_on_approved_plan(installment)

    if approve:
        current_due_date = effective_due_date(installment)
        if request.proposed_due_date == current_due_date:
            raise ValueError(
                "The proposed due date matches the current effective due date; nothing to apply."
            )
        InstallmentDueDateRevision.objects.create(
            installment=installment,
            previous_due_date=current_due_date,
            revised_due_date=request.proposed_due_date,
            reason=request.reason,
            revised_by=actor,
            source_request=request,
        )
        request.status = DueDateChangeRequest.Status.APPROVED
    else:
        request.status = DueDateChangeRequest.Status.REJECTED

    request.decided_by = actor
    request.decided_at = timezone.now()
    request.decision_note = (note or "").strip()
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return request


@transaction.atomic
def apply_due_date_revision(
    installment: FeeInstallment,
    *,
    revised_due_date,
    reason,
    actor,
) -> InstallmentDueDateRevision:
    """
    Apply a due-date change directly for ``ADMIN``/``MANAGEMENT``/``ACCOUNTS``.

    Earlier and later dates are both allowed. Amounts and the original
    suggested date are never touched.
    """
    _require_role(actor, DUE_DATE_APPLIER_ROLES, "apply due-date revisions")

    installment = (
        FeeInstallment.objects.select_for_update()
        .select_related("fee_plan__admission__centre")
        .get(pk=installment.pk)
    )
    _require_centre_access(actor, installment.fee_plan.admission)
    _require_course_installment_on_approved_plan(installment)
    reason = _validate_reason(reason)
    if revised_due_date is None:
        raise ValueError("A revised due date is required.")

    current_due_date = effective_due_date(installment)
    if revised_due_date == current_due_date:
        raise ValueError("The revised due date matches the current effective due date.")

    return InstallmentDueDateRevision.objects.create(
        installment=installment,
        previous_due_date=current_due_date,
        revised_due_date=revised_due_date,
        reason=reason,
        revised_by=actor,
        source_request=None,
    )

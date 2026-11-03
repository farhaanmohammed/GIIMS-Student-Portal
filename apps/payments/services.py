"""
Payment collection, allocation, advance-credit, refund, and reversal services.

Every function is atomic, re-validates server-side, and never trusts
client-submitted totals, receipt numbers, or outstanding balances. Money is
always handled as ``Decimal`` quantized to two places.

Rounding policy: half-up to two decimal places (``MONEY_QUANTUM``).
Operating currency: INR.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.admissions.models import Admission
from apps.centres.models import Centre
from apps.fees.models import FeeInstallment, FeePlan
from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
    Payment,
    PaymentMode,
    PaymentReversal,
    Receipt,
    ReceiptSequence,
    Refund,
    RefundCreditAllocation,
)

PAYMENT_ROLES = frozenset({"ADMIN", "MANAGEMENT", "ACCOUNTS"})
APPROVER_ROLES = frozenset({"ADMIN", "MANAGEMENT"})
MIN_REASON_LENGTH = 10
MONEY_QUANTUM = Decimal("0.01")
ZERO = Decimal("0.00")
FINANCIAL_YEAR_START_MONTH = 4


def _is_authenticated(actor) -> bool:
    return bool(actor is not None and getattr(actor, "is_authenticated", False))


def _has_role(actor, roles: frozenset[str]) -> bool:
    return bool(actor.is_superuser or getattr(actor, "role", None) in roles)


def _require_role(actor, roles: frozenset[str], action: str) -> None:
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")
    if not _has_role(actor, roles):
        raise PermissionDenied(f"You do not have permission to {action}.")


def _require_centre_access(actor, centre) -> None:
    if not _is_authenticated(actor):
        raise PermissionDenied("Authentication required.")
    if not actor.can_access_centre(centre):
        raise PermissionDenied("You do not have access to this centre.")


def _resolve_centre(centre) -> Centre:
    if isinstance(centre, Centre):
        return centre
    return Centre.objects.get(pk=centre)


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Enter a valid monetary amount.") from exc


def _validate_reason(reason) -> str:
    cleaned = (reason or "").strip()
    if len(cleaned) < MIN_REASON_LENGTH:
        raise ValueError(f"A reason of at least {MIN_REASON_LENGTH} characters is required.")
    return cleaned


def _validate_mode(mode) -> str:
    valid = set(PaymentMode.values)
    if mode not in valid:
        raise ValueError("Choose a valid payment mode.")
    return mode


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return timezone.localtime(value).date()
    if isinstance(value, date):
        return value
    raise ValueError("Enter a valid payment date.")


def financial_year_for(on_date: date) -> str:
    """
    Indian financial year label for a date: 1 April to 31 March.

    ``2026-07-15`` and ``2027-01-20`` both return ``FY26-27``.
    """
    on_date = _as_date(on_date)
    start_year = on_date.year if on_date.month >= FINANCIAL_YEAR_START_MONTH else on_date.year - 1
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def build_receipt_number(centre_code: str, financial_year: str, sequence: int) -> str:
    """``GIIMS/{CENTRE_CODE}/{FINANCIAL_YEAR}/{SEQUENCE}`` with six digits."""
    return f"GIIMS/{centre_code}/{financial_year}/{sequence:06d}"


def _next_receipt_sequence(centre, financial_year: str) -> int:
    """
    Reserve the next receipt number for a centre and financial year.

    The counter row is locked for the duration of the enclosing transaction,
    so two concurrent collections in the same centre and year cannot receive
    the same number. The first use of a centre/year creates the row inside a
    savepoint so a concurrent creator does not abort the whole payment.
    """
    try:
        sequence = ReceiptSequence.objects.select_for_update().get(
            centre=centre, financial_year=financial_year
        )
    except ReceiptSequence.DoesNotExist:
        try:
            with transaction.atomic():
                ReceiptSequence.objects.create(
                    centre=centre,
                    financial_year=financial_year,
                    last_sequence=1,
                )
            return 1
        except IntegrityError:
            sequence = ReceiptSequence.objects.select_for_update().get(
                centre=centre, financial_year=financial_year
            )

    sequence.last_sequence += 1
    sequence.save(update_fields=["last_sequence", "updated_at"])
    return sequence.last_sequence


def _current_plan(admission: Admission) -> FeePlan:
    plan = (
        FeePlan.objects.select_for_update()
        .filter(admission=admission, status=FeePlan.Status.APPROVED)
        .first()
    )
    if plan is None:
        raise ValueError("An approved fee plan is required before collecting a payment.")
    return plan


def _lock_installments(plan: FeePlan) -> list[FeeInstallment]:
    return list(
        FeeInstallment.objects.select_for_update().filter(fee_plan=plan).order_by("sequence", "pk")
    )


def _allocated_amounts(installments: list[FeeInstallment]) -> dict[int, Decimal]:
    """Active allocation totals for the supplied installments, one query."""
    totals = {installment.pk: ZERO for installment in installments}
    rows = (
        InstallmentAllocation.objects.filter(
            installment_id__in=totals,
            status=InstallmentAllocation.Status.ACTIVE,
        )
        .values("installment_id")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        totals[row["installment_id"]] = _money(row["total"])
    return totals


def _outstanding_map(installments: list[FeeInstallment]) -> dict[int, Decimal]:
    allocated = _allocated_amounts(installments)
    return {
        installment.pk: max(installment.amount - allocated[installment.pk], ZERO)
        for installment in installments
    }


def _fifo_plan(
    installments: list[FeeInstallment],
    outstanding: dict[int, Decimal],
    amount: Decimal,
) -> list[tuple[FeeInstallment, Decimal]]:
    """
    Suggest allocations oldest-first.

    Sequence order is the agreed order: application charge, admission charge,
    then the four dated course installments.
    """
    rows: list[tuple[FeeInstallment, Decimal]] = []
    remaining = amount
    for installment in installments:
        if remaining <= ZERO:
            break
        available = outstanding.get(installment.pk, ZERO)
        if available <= ZERO:
            continue
        take = min(available, remaining)
        rows.append((installment, take))
        remaining -= take
    return rows


def _normalize_allocations(allocations) -> list[tuple[int, Decimal]]:
    """Accept ``{"installment"|"installment_id", "amount"}`` mappings."""
    rows: list[tuple[int, Decimal]] = []
    for item in allocations or []:
        if item is None:
            continue
        try:
            if isinstance(item, dict):
                raw_installment = item.get("installment") or item.get("installment_id")
                raw_amount = item.get("amount")
            else:
                raw_installment, raw_amount = item
            installment_id = int(getattr(raw_installment, "pk", raw_installment))
            amount = _money(raw_amount)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Each allocation needs an installment and an amount.") from exc
        if amount == ZERO:
            continue
        if amount < ZERO:
            raise ValueError("Allocation amounts cannot be negative.")
        rows.append((installment_id, amount))
    return rows


def _create_audit_event(
    *,
    admission: Admission,
    event_type: str,
    actor,
    payment: Payment | None = None,
    installment: FeeInstallment | None = None,
    amount: Decimal | None = None,
    detail: str = "",
) -> FinancialAuditEvent:
    return FinancialAuditEvent.objects.create(
        admission=admission,
        event_type=event_type,
        actor=actor,
        payment=payment,
        installment=installment,
        amount=amount,
        detail=detail,
    )


@transaction.atomic
def record_payment(
    admission: Admission,
    *,
    amount,
    paid_on,
    mode,
    collection_centre,
    actor,
    reference: str = "",
    notes: str = "",
    allocations=None,
    request_key: str | None = None,
) -> Payment:
    """
    Record a collection, issue a receipt, allocate it, and retain any excess.

    ``allocations`` is a list of ``{"installment": <id|object>, "amount": ...}``.
    When it is ``None`` the service applies FIFO automatically. An empty list
    means "allocate nothing"; the full amount is retained as advance credit.
    A submitted allocation may never exceed an installment's outstanding
    balance; the service raises instead of silently capping.
    """
    _require_role(actor, PAYMENT_ROLES, "record payments")
    mode = _validate_mode(mode)
    total = _money(amount)
    if total <= ZERO:
        raise ValueError("A payment amount greater than zero is required.")
    paid_on = _as_date(paid_on)
    collection_centre = _resolve_centre(collection_centre)

    admission = (
        Admission.objects.select_for_update()
        .select_related("centre", "student", "course", "batch")
        .get(pk=admission.pk)
    )
    _require_centre_access(actor, admission.centre)
    _require_centre_access(actor, collection_centre)

    if admission.status != Admission.Status.ENROLLED:
        raise ValueError("Payments can only be recorded for an enrolled admission.")

    if request_key:
        existing = Payment.objects.filter(request_key=request_key).first()
        if existing is not None:
            if existing.admission_id != admission.pk:
                raise ValueError("This idempotency key belongs to another admission.")
            return existing

    plan = _current_plan(admission)
    installments = _lock_installments(plan)
    outstanding = _outstanding_map(installments)

    payment = Payment.objects.create(
        centre=collection_centre,
        admission=admission,
        amount=total,
        paid_on=paid_on,
        mode=mode,
        reference=(reference or "").strip(),
        notes=(notes or "").strip(),
        recorded_by=actor,
        request_key=request_key or None,
    )

    financial_year = financial_year_for(paid_on)
    sequence = _next_receipt_sequence(collection_centre, financial_year)
    receipt = Receipt.objects.create(
        payment=payment,
        centre=collection_centre,
        financial_year=financial_year,
        sequence=sequence,
        number=build_receipt_number(collection_centre.code, financial_year, sequence),
        issued_by=actor,
    )
    _create_audit_event(
        admission=admission,
        event_type=FinancialAuditEvent.Event.PAYMENT_RECORDED,
        actor=actor,
        payment=payment,
        amount=total,
        detail=f"{payment.get_mode_display()} collection at {collection_centre.code}.",
    )
    _create_audit_event(
        admission=admission,
        event_type=FinancialAuditEvent.Event.RECEIPT_ISSUED,
        actor=actor,
        payment=payment,
        amount=total,
        detail=receipt.number,
    )

    by_pk = {installment.pk: installment for installment in installments}
    if allocations is None:
        requested = _fifo_plan(installments, outstanding, total)
    else:
        normalized = _normalize_allocations(allocations)
        # Merge repeated rows for one installment before validating, so two
        # rows can never combine to exceed that installment's outstanding
        # balance.
        merged: dict[int, Decimal] = {}
        for installment_id, allocated in normalized:
            if installment_id not in by_pk:
                raise ValueError("That installment does not belong to the approved fee plan.")
            merged[installment_id] = merged.get(installment_id, ZERO) + allocated
        requested = []
        for installment_id, allocated in merged.items():
            installment = by_pk[installment_id]
            if allocated > outstanding[installment.pk]:
                raise ValueError(
                    f"Allocation exceeds the outstanding balance of {installment.description}."
                )
            requested.append((installment, allocated))

    allocated_total = sum((allocated for _, allocated in requested), ZERO)
    if allocated_total > total:
        raise ValueError("Allocations cannot exceed the payment amount.")

    for installment, allocated in requested:
        if allocated <= ZERO:
            continue
        allocation = InstallmentAllocation.objects.create(
            installment=installment,
            amount=allocated,
            source_type=InstallmentAllocation.Source.PAYMENT,
            payment=payment,
            allocated_by=actor,
        )
        _create_audit_event(
            admission=admission,
            event_type=FinancialAuditEvent.Event.ALLOCATION_CREATED,
            actor=actor,
            payment=payment,
            installment=installment,
            amount=allocated,
            detail=f"Allocation {allocation.pk} from payment {payment.pk}.",
        )

    excess = total - allocated_total
    if excess > ZERO:
        credit = AdvanceCredit.objects.create(
            admission=admission,
            source_payment=payment,
            source_type=AdvanceCredit.Source.OVERPAYMENT,
            original_amount=excess,
            remaining_amount=excess,
            created_by=actor,
            notes="Retained from an overpayment.",
        )
        _create_audit_event(
            admission=admission,
            event_type=FinancialAuditEvent.Event.CREDIT_CREATED,
            actor=actor,
            payment=payment,
            amount=excess,
            detail=f"Advance credit lot {credit.pk} created.",
        )

    return payment


@transaction.atomic
def apply_advance_credit(
    installment: FeeInstallment,
    *,
    amount,
    actor,
    request_key: str,
    reason: str = "",
) -> Decimal:
    """
    Apply available advance credit to an installment.

    Credit lots are consumed oldest-first. The request key makes retries
    idempotent and the ``(request_key, credit)`` database constraint prevents
    the same credit lot from being applied twice for one request.
    """
    _require_role(actor, PAYMENT_ROLES, "apply advance credit")
    total = _money(amount)
    if total <= ZERO:
        raise ValueError("An amount greater than zero is required.")
    if not request_key:
        raise ValueError("A request key is required for credit applications.")

    target = FeeInstallment.objects.select_related("fee_plan__admission__centre").get(
        pk=installment.pk
    )
    admission = (
        Admission.objects.select_for_update()
        .select_related("centre")
        .get(pk=target.fee_plan.admission_id)
    )
    _require_centre_access(actor, admission.centre)

    existing_rows = list(
        InstallmentAllocation.objects.filter(
            request_key=request_key,
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
        )
    )
    if existing_rows:
        if any(row.installment_id != target.pk for row in existing_rows):
            raise ValueError("This idempotency key was used for another installment.")
        return _money(sum((row.amount for row in existing_rows), ZERO))

    installment = (
        FeeInstallment.objects.select_for_update().select_related("fee_plan").get(pk=target.pk)
    )
    if installment.fee_plan.status != FeePlan.Status.APPROVED:
        raise ValueError("Credit can only be applied to an approved fee plan.")

    outstanding = _outstanding_map([installment])[installment.pk]
    if outstanding <= ZERO:
        raise ValueError("This installment has no outstanding balance.")
    if total > outstanding:
        raise ValueError("Credit application exceeds the installment's outstanding balance.")

    lots = list(
        AdvanceCredit.objects.select_for_update()
        .filter(admission=admission, status=AdvanceCredit.Status.ACTIVE)
        .order_by("created_at", "pk")
    )
    available = sum((lot.remaining_amount for lot in lots), ZERO)
    if total > available:
        raise ValueError("The requested amount exceeds the available advance credit.")

    remaining = total
    for lot in lots:
        if remaining <= ZERO:
            break
        take = min(lot.remaining_amount, remaining)
        if take <= ZERO:
            continue
        allocation = InstallmentAllocation.objects.create(
            installment=installment,
            amount=take,
            source_type=InstallmentAllocation.Source.ADVANCE_CREDIT,
            credit=lot,
            request_key=request_key,
            allocated_by=actor,
        )
        lot.remaining_amount -= take
        if lot.remaining_amount <= ZERO:
            lot.remaining_amount = ZERO
            lot.status = AdvanceCredit.Status.DEPLETED
        lot.save(update_fields=["remaining_amount", "status", "updated_at"])
        _create_audit_event(
            admission=admission,
            event_type=FinancialAuditEvent.Event.CREDIT_APPLIED,
            actor=actor,
            installment=installment,
            amount=take,
            detail=f"Credit lot {lot.pk} applied (allocation {allocation.pk}). {reason}".strip(),
        )
        remaining -= take

    return total


@transaction.atomic
def request_refund(
    credit: AdvanceCredit,
    *,
    amount,
    reason,
    mode,
    actor,
    request_key: str,
) -> Refund:
    """Raise a refund request against an available credit lot."""
    _require_role(actor, PAYMENT_ROLES, "request refunds")
    total = _money(amount)
    if total <= ZERO:
        raise ValueError("A refund amount greater than zero is required.")
    reason = _validate_reason(reason)
    mode = _validate_mode(mode)
    if not request_key:
        raise ValueError("A request key is required for refund requests.")

    target = AdvanceCredit.objects.select_related("admission__centre", "source_payment").get(
        pk=credit.pk
    )
    admission = (
        Admission.objects.select_for_update().select_related("centre").get(pk=target.admission_id)
    )
    _require_centre_access(actor, admission.centre)
    credit = AdvanceCredit.objects.select_for_update().get(pk=target.pk)

    existing = Refund.objects.filter(request_key=request_key).first()
    if existing is not None:
        if existing.credit_id != credit.pk:
            raise ValueError("This idempotency key belongs to another refund request.")
        return existing

    if credit.status == AdvanceCredit.Status.REVERSED:
        raise ValueError("Credit from a reversed payment cannot be refunded.")
    if total > credit.remaining_amount:
        raise ValueError("Refund exceeds the available refundable credit.")

    refund = Refund.objects.create(
        admission=admission,
        credit=credit,
        original_payment=credit.source_payment,
        amount=total,
        reason=reason,
        mode=mode,
        requested_by=actor,
        request_key=request_key,
    )
    _create_audit_event(
        admission=credit.admission,
        event_type=FinancialAuditEvent.Event.REFUND_REQUESTED,
        actor=actor,
        payment=credit.source_payment,
        amount=total,
        detail=f"Refund {refund.pk} requested from credit lot {credit.pk}.",
    )
    return refund


@transaction.atomic
def decide_refund(
    refund: Refund,
    *,
    approve: bool,
    actor,
    note: str = "",
    refunded_on=None,
) -> Refund:
    """Approve (reducing credit) or reject a refund request."""
    _require_role(actor, APPROVER_ROLES, "decide refunds")

    target = Refund.objects.select_related("admission__centre").get(pk=refund.pk)
    admission = (
        Admission.objects.select_for_update().select_related("centre").get(pk=target.admission_id)
    )
    _require_centre_access(actor, admission.centre)
    refund = (
        Refund.objects.select_for_update()
        .select_related("credit__admission__centre", "admission__centre")
        .get(pk=target.pk)
    )
    if refund.status != Refund.Status.PENDING:
        raise ValueError("Only pending refunds can be decided.")

    if approve:
        credit = AdvanceCredit.objects.select_for_update().get(pk=refund.credit_id)
        if credit.status == AdvanceCredit.Status.REVERSED:
            raise ValueError("The source credit has been reversed and cannot be refunded.")
        if refund.amount > credit.remaining_amount:
            raise ValueError("Refund exceeds the available refundable credit.")

        credit.remaining_amount -= refund.amount
        if credit.remaining_amount <= ZERO:
            credit.remaining_amount = ZERO
            credit.status = AdvanceCredit.Status.DEPLETED
        credit.save(update_fields=["remaining_amount", "status", "updated_at"])
        RefundCreditAllocation.objects.create(
            refund=refund,
            credit=credit,
            amount=refund.amount,
        )
        refund.status = Refund.Status.APPROVED
        refund.refunded_on = _as_date(refunded_on) if refunded_on else timezone.localdate()
        _create_audit_event(
            admission=refund.admission,
            event_type=FinancialAuditEvent.Event.REFUND_APPROVED,
            actor=actor,
            payment=refund.original_payment,
            amount=refund.amount,
            detail=f"Refund {refund.pk} approved. {note}".strip(),
        )
    else:
        refund.status = Refund.Status.REJECTED
        _create_audit_event(
            admission=refund.admission,
            event_type=FinancialAuditEvent.Event.REFUND_REJECTED,
            actor=actor,
            payment=refund.original_payment,
            amount=refund.amount,
            detail=f"Refund {refund.pk} rejected. {note}".strip(),
        )

    refund.decided_by = actor
    refund.decided_at = timezone.now()
    refund.decision_note = (note or "").strip()
    refund.save(
        update_fields=["status", "decided_by", "decided_at", "decision_note", "refunded_on"]
    )
    return refund


@transaction.atomic
def request_payment_reversal(
    payment: Payment,
    *,
    reason,
    actor,
    request_key: str,
) -> PaymentReversal:
    """Raise a reversal request for a posted payment."""
    _require_role(actor, PAYMENT_ROLES, "request payment reversals")
    reason = _validate_reason(reason)
    if not request_key:
        raise ValueError("A request key is required for reversal requests.")

    payment = (
        Payment.objects.select_for_update()
        .select_related("admission__centre", "centre")
        .get(pk=payment.pk)
    )
    _require_centre_access(actor, payment.admission.centre)

    existing = PaymentReversal.objects.filter(request_key=request_key).first()
    if existing is not None:
        if existing.payment_id != payment.pk:
            raise ValueError("This idempotency key belongs to another payment reversal.")
        return existing

    if payment.status != Payment.Status.POSTED:
        raise ValueError("Only a posted payment can be reversed.")
    if PaymentReversal.objects.filter(payment=payment).exists():
        raise ValueError("A reversal already exists for this payment.")

    if Refund.objects.filter(
        credit__source_payment=payment,
        status__in=(Refund.Status.PENDING, Refund.Status.APPROVED),
    ).exists():
        raise ValueError(
            "This payment has refunds against its credit. Resolve them before reversing."
        )

    from apps.fees.models import FeePlanReassignment

    if FeePlanReassignment.objects.filter(
        admission=payment.admission,
        status=FeePlanReassignment.Status.APPROVED,
    ).exists():
        raise ValueError(
            "This admission has an approved fee-plan reassignment; "
            "reverse it through the reconciliation process instead."
        )

    reversal = PaymentReversal.objects.create(
        payment=payment,
        amount=payment.amount,
        reason=reason,
        requested_by=actor,
        request_key=request_key,
    )
    _create_audit_event(
        admission=payment.admission,
        event_type=FinancialAuditEvent.Event.REVERSAL_REQUESTED,
        actor=actor,
        payment=payment,
        amount=payment.amount,
        detail=f"Reversal {reversal.pk} requested.",
    )
    return reversal


def _reverse_allocation(allocation: InstallmentAllocation, *, actor, reason: str) -> None:
    allocation.status = InstallmentAllocation.Status.REVERSED
    allocation.reversed_by = actor
    allocation.reversed_at = timezone.now()
    allocation.reversal_reason = reason
    allocation.save(
        update_fields=["status", "reversed_by", "reversed_at", "reversal_reason", "updated_at"]
    )


@transaction.atomic
def decide_payment_reversal(
    reversal: PaymentReversal,
    *,
    approve: bool,
    actor,
    note: str = "",
) -> PaymentReversal:
    """
    Approve (reversing allocations, credit, and receipt status) or reject.

    Nothing is deleted. Direct payment allocations are marked reversed, credit
    applications funded by the payment's lot are marked reversed and returned
    to the lot, then the lot is zeroed and marked reversed. The receipt keeps
    its original number and becomes ``REVERSED``.
    """
    _require_role(actor, APPROVER_ROLES, "decide payment reversals")

    reversal = (
        PaymentReversal.objects.select_for_update()
        .select_related("payment__admission__centre")
        .get(pk=reversal.pk)
    )
    _require_centre_access(actor, reversal.payment.admission.centre)
    if reversal.status != PaymentReversal.Status.PENDING:
        raise ValueError("Only pending reversals can be decided.")

    if approve:
        payment = Payment.objects.select_for_update().get(pk=reversal.payment_id)
        admission = (
            Admission.objects.select_for_update()
            .select_related("centre")
            .get(pk=payment.admission_id)
        )
        plan = (
            FeePlan.objects.select_for_update()
            .filter(admission=admission, status=FeePlan.Status.APPROVED)
            .first()
        )
        if plan is not None:
            _lock_installments(plan)

        reason = f"Payment reversal {reversal.pk}: {reversal.reason}"
        for allocation in InstallmentAllocation.objects.select_for_update().filter(
            payment=payment,
            status=InstallmentAllocation.Status.ACTIVE,
        ):
            _reverse_allocation(allocation, actor=actor, reason=reason)

        credit = AdvanceCredit.objects.select_for_update().filter(source_payment=payment).first()
        if credit is not None:
            for allocation in InstallmentAllocation.objects.select_for_update().filter(
                credit=credit,
                status=InstallmentAllocation.Status.ACTIVE,
            ):
                _reverse_allocation(allocation, actor=actor, reason=reason)
            credit.remaining_amount = ZERO
            credit.status = AdvanceCredit.Status.REVERSED
            credit.save(update_fields=["remaining_amount", "status", "updated_at"])

        receipt = Receipt.objects.select_for_update().get(payment=payment)
        receipt.status = Receipt.Status.REVERSED
        receipt.save(update_fields=["status", "updated_at"])

        payment.status = Payment.Status.REVERSED
        payment.save(update_fields=["status", "updated_at"])

        reversal.status = PaymentReversal.Status.APPROVED
        _create_audit_event(
            admission=admission,
            event_type=FinancialAuditEvent.Event.REVERSAL_APPROVED,
            actor=actor,
            payment=payment,
            amount=payment.amount,
            detail=f"Reversal {reversal.pk} approved. {note}".strip(),
        )
    else:
        reversal.status = PaymentReversal.Status.REJECTED
        _create_audit_event(
            admission=reversal.payment.admission,
            event_type=FinancialAuditEvent.Event.REVERSAL_REJECTED,
            actor=actor,
            payment=reversal.payment,
            amount=reversal.amount,
            detail=f"Reversal {reversal.pk} rejected. {note}".strip(),
        )

    reversal.decided_by = actor
    reversal.decided_at = timezone.now()
    reversal.decision_note = (note or "").strip()
    reversal.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return reversal


@transaction.atomic
def apply_credit_fifo(
    admission: Admission,
    *,
    actor,
    request_key_prefix: str,
    plan: FeePlan | None = None,
) -> Decimal:
    """
    Apply every available credit lot to the approved plan, oldest first.

    Used by the reassignment reconciliation. Each installment gets its own
    idempotency key derived from ``request_key_prefix``.
    """
    _require_role(actor, PAYMENT_ROLES, "apply advance credit")

    admission = Admission.objects.select_for_update().select_related("centre").get(pk=admission.pk)
    plan = plan or _current_plan(admission)
    installments = _lock_installments(plan)
    available = sum(
        (
            lot.remaining_amount
            for lot in AdvanceCredit.objects.select_for_update().filter(
                admission=admission, status=AdvanceCredit.Status.ACTIVE
            )
        ),
        ZERO,
    )
    if available <= ZERO:
        return ZERO

    outstanding = _outstanding_map(installments)
    applied = ZERO
    for installment in installments:
        if available <= ZERO:
            break
        capacity = outstanding[installment.pk]
        if capacity <= ZERO:
            continue
        take = min(capacity, available)
        applied += apply_advance_credit(
            installment,
            amount=take,
            actor=actor,
            request_key=f"{request_key_prefix}:{installment.pk}",
            reason="Fee-plan reassignment reconciliation.",
        )
        available -= take
    return applied

"""
Centre-scoped selectors for student ledgers, financial reports, and audit trails.

Every selector enforces centre scoping server-side through the actor's
``get_accessible_centres()`` queryset and uses ``Decimal`` arithmetic with
``select_related``/``prefetch_related`` to prevent N+1 queries.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.admissions.models import Admission
from apps.fees.models import (
    DueDateChangeRequest,
    FeeInstallment,
    FeePlan,
    FeePlanReassignment,
    InstallmentDueDateRevision,
)
from apps.fees.selectors import FeeSummary
from apps.payments.models import (
    AdvanceCredit,
    FinancialAuditEvent,
    InstallmentAllocation,
    Payment,
    PaymentReversal,
    Refund,
)

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LedgerInstallmentRow:
    """One installment row on a student's fee ledger."""

    installment: FeeInstallment
    original_due_date: date | None
    effective_due_date: date | None
    billed: Decimal
    allocated: Decimal
    outstanding: Decimal
    is_overdue: bool
    days_overdue: int


@dataclass(frozen=True)
class StudentLedgerData:
    """Complete as-of ledger snapshot for one admission."""

    admission: Admission
    as_of: date
    current_plan: FeePlan | None
    plan_versions: list[FeePlan]
    summary: FeeSummary
    installment_rows: list[LedgerInstallmentRow]
    payments: list[Payment]
    allocations: list[InstallmentAllocation]
    credit_lots: list[AdvanceCredit]
    refunds: list[Refund]
    reversals: list[PaymentReversal]
    reassignments: list[FeePlanReassignment]
    due_date_revisions: list[InstallmentDueDateRevision]
    audit_events: list[FinancialAuditEvent]


@dataclass(frozen=True)
class OutstandingAdmissionRow:
    """One admission's balance breakdown for the Outstanding Report."""

    admission: Admission
    plan: FeePlan
    total_billed: Decimal
    total_paid: Decimal
    total_outstanding: Decimal
    overdue_outstanding: Decimal
    not_yet_due_outstanding: Decimal
    undated_outstanding: Decimal
    available_credit: Decimal


@dataclass(frozen=True)
class OutstandingReportData:
    """Rows and aggregate totals for the Outstanding Report."""

    as_of: date
    rows: list[OutstandingAdmissionRow]
    total_billed: Decimal
    total_paid: Decimal
    total_outstanding: Decimal
    overdue_outstanding: Decimal
    not_yet_due_outstanding: Decimal
    undated_outstanding: Decimal
    available_credit: Decimal


@dataclass(frozen=True)
class OverdueInstallmentRow:
    """One overdue installment row as of the selected date."""

    admission: Admission
    installment: FeeInstallment
    original_due_date: date
    effective_due_date: date
    days_overdue: int
    billed: Decimal
    paid: Decimal
    overdue_amount: Decimal


@dataclass(frozen=True)
class CollectionSummaryRow:
    """Centre or daily aggregate row for the Collection Report."""

    label: str
    code: str
    posted_count: int
    posted_amount: Decimal
    reversed_count: int
    reversed_amount: Decimal


@dataclass(frozen=True)
class CollectionReportData:
    """Collection register plus collection-centre, admission-centre, and daily totals."""

    payments: list[Payment]
    by_collection_centre: list[CollectionSummaryRow]
    by_admission_centre: list[CollectionSummaryRow]
    by_day: list[CollectionSummaryRow]
    by_mode: list[CollectionSummaryRow]
    posted_count: int
    posted_total: Decimal
    reversed_count: int
    reversed_total: Decimal


def parse_report_date(raw: str | date | None, default: date | None = None) -> date:
    """Parse an ISO ``YYYY-MM-DD`` date or fall back to ``default`` / today."""
    fallback = default or timezone.localdate()
    if isinstance(raw, date):
        return raw
    if not raw or not isinstance(raw, str):
        return fallback
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return fallback


def _effective_date_from_prefetched(installment: FeeInstallment) -> date | None:
    """Compute effective due date using prefetched revisions when available."""
    if installment.category != FeeInstallment.Category.COURSE:
        return None
    revisions = list(installment.due_date_revisions.all())
    if revisions:
        revisions.sort(key=lambda r: (r.created_at, r.pk), reverse=True)
        return revisions[0].revised_due_date
    return installment.original_due_date


def _active_allocated_from_prefetched(installment: FeeInstallment) -> Decimal:
    """Sum active allocations from prefetched rows without extra queries."""
    total = ZERO
    for alloc in installment.allocations.all():
        if alloc.status == InstallmentAllocation.Status.ACTIVE:
            total += alloc.amount
    return total


def build_student_ledger(
    admission: Admission,
    *,
    as_of: date | None = None,
) -> StudentLedgerData:
    """
    Build the complete fee ledger for ``admission`` as of ``as_of``.

    Uses bulk queries on the admission's plans, installments, payments,
    allocations, credit lots, refunds, reversals, reassignments, and audit
    events.
    """
    as_of = parse_report_date(as_of)
    plan_versions = list(
        FeePlan.objects.filter(admission=admission)
        .select_related("approved_by", "supersedes")
        .prefetch_related(
            "installments__due_date_revisions__revised_by",
            "installments__allocations",
        )
        .order_by("version")
    )
    current_plan = next(
        (plan for plan in plan_versions if plan.status == FeePlan.Status.APPROVED),
        None,
    )

    credit_lots = list(
        AdvanceCredit.objects.filter(admission=admission)
        .select_related("source_payment__receipt", "created_by")
        .order_by("created_at", "pk")
    )
    available_cred = sum(
        (lot.remaining_amount for lot in credit_lots if lot.status == AdvanceCredit.Status.ACTIVE),
        ZERO,
    )

    installment_rows: list[LedgerInstallmentRow] = []
    total_billed = ZERO
    total_outstanding = ZERO
    overdue_outstanding = ZERO
    not_yet_due_outstanding = ZERO
    undated_outstanding = ZERO

    if current_plan is not None:
        installments = sorted(current_plan.installments.all(), key=lambda i: i.sequence)
        for inst in installments:
            allocated = _active_allocated_from_prefetched(inst)
            outstanding = max(inst.amount - allocated, ZERO)
            eff_date = _effective_date_from_prefetched(inst)
            is_overdue = bool(outstanding > ZERO and eff_date is not None and eff_date < as_of)
            days_overdue = (as_of - eff_date).days if is_overdue and eff_date else 0

            total_billed += inst.amount
            total_outstanding += outstanding
            if outstanding > ZERO:
                if eff_date is None:
                    undated_outstanding += outstanding
                elif eff_date < as_of:
                    overdue_outstanding += outstanding
                else:
                    not_yet_due_outstanding += outstanding

            installment_rows.append(
                LedgerInstallmentRow(
                    installment=inst,
                    original_due_date=inst.original_due_date,
                    effective_due_date=eff_date,
                    billed=inst.amount,
                    allocated=allocated,
                    outstanding=outstanding,
                    is_overdue=is_overdue,
                    days_overdue=days_overdue,
                )
            )

    summary = FeeSummary(
        as_of=as_of,
        total_billed=total_billed,
        total_paid=total_billed - total_outstanding,
        total_outstanding=total_outstanding,
        overdue_outstanding=overdue_outstanding,
        not_yet_due_outstanding=not_yet_due_outstanding,
        undated_outstanding=undated_outstanding,
        available_credit=available_cred,
    )

    payments = list(
        Payment.objects.filter(admission=admission)
        .select_related("centre", "recorded_by", "receipt")
        .order_by("-paid_on", "-posted_at", "-pk")
    )
    allocations = list(
        InstallmentAllocation.objects.filter(installment__fee_plan__admission=admission)
        .select_related(
            "installment__fee_plan",
            "payment__receipt",
            "credit__source_payment__receipt",
            "allocated_by",
            "reversed_by",
        )
        .order_by("-allocated_at", "-pk")
    )
    refunds = list(
        Refund.objects.filter(admission=admission)
        .select_related("credit", "original_payment__receipt", "requested_by", "decided_by")
        .order_by("-requested_at", "-pk")
    )
    reversals = list(
        PaymentReversal.objects.filter(payment__admission=admission)
        .select_related("payment__receipt", "requested_by", "decided_by")
        .order_by("-requested_at", "-pk")
    )
    reassignments = list(
        FeePlanReassignment.objects.filter(admission=admission)
        .select_related("from_plan", "to_plan", "requested_by", "decided_by")
        .order_by("-requested_at", "-pk")
    )
    due_date_revisions = list(
        InstallmentDueDateRevision.objects.filter(installment__fee_plan__admission=admission)
        .select_related("installment__fee_plan", "revised_by", "source_request")
        .order_by("-created_at", "-pk")
    )
    audit_events = list(
        FinancialAuditEvent.objects.filter(admission=admission)
        .select_related("actor", "payment__receipt", "installment")
        .order_by("-created_at", "-pk")
    )

    return StudentLedgerData(
        admission=admission,
        as_of=as_of,
        current_plan=current_plan,
        plan_versions=plan_versions,
        summary=summary,
        installment_rows=installment_rows,
        payments=payments,
        allocations=allocations,
        credit_lots=credit_lots,
        refunds=refunds,
        reversals=reversals,
        reassignments=reassignments,
        due_date_revisions=due_date_revisions,
        audit_events=audit_events,
    )


def admissions_register_queryset(
    user,
    *,
    centre_id: int | None = None,
    course_id: int | None = None,
    batch_id: int | None = None,
    academic_year_id: int | None = None,
    status: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
):
    """Centre-scoped admission register queryset with filters."""
    queryset = (
        Admission.objects.for_user(user)
        .select_related(
            "student",
            "centre",
            "course",
            "batch",
            "academic_year",
            "enrolled_by",
        )
        .order_by("-enquired_at", "-pk")
    )
    if centre_id:
        queryset = queryset.filter(centre_id=centre_id)
    if course_id:
        queryset = queryset.filter(course_id=course_id)
    if batch_id:
        queryset = queryset.filter(batch_id=batch_id)
    if academic_year_id:
        queryset = queryset.filter(academic_year_id=academic_year_id)
    if status in dict(Admission.Status.choices):
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(enquired_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(enquired_at__date__lte=date_to)
    if query:
        queryset = queryset.filter(
            Q(student__student_id__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(student__phone__icontains=query)
            | Q(course__code__icontains=query)
            | Q(batch__code__icontains=query)
        )
    return queryset


def build_outstanding_report(
    user,
    *,
    as_of: date | None = None,
    centre_id: int | None = None,
    course_id: int | None = None,
    batch_id: int | None = None,
    academic_year_id: int | None = None,
    only_outstanding: bool = False,
    query: str = "",
) -> OutstandingReportData:
    """
    Calculate admission-wise and aggregate outstanding balances as of ``as_of``.

    Uses prefetched installments, revisions, and allocations plus grouped credit
    sums so no N+1 queries are executed per student.
    """
    as_of = parse_report_date(as_of)
    plans_qs = (
        FeePlan.objects.filter(
            status=FeePlan.Status.APPROVED,
            admission__centre__in=user.get_accessible_centres(),
        )
        .select_related(
            "admission__student",
            "admission__centre",
            "admission__course",
            "admission__batch",
            "admission__academic_year",
        )
        .prefetch_related(
            "installments__due_date_revisions",
            "installments__allocations",
        )
        .order_by("admission__centre__code", "admission__student__first_name", "pk")
    )
    if centre_id:
        plans_qs = plans_qs.filter(admission__centre_id=centre_id)
    if course_id:
        plans_qs = plans_qs.filter(admission__course_id=course_id)
    if batch_id:
        plans_qs = plans_qs.filter(admission__batch_id=batch_id)
    if academic_year_id:
        plans_qs = plans_qs.filter(admission__academic_year_id=academic_year_id)
    if query:
        plans_qs = plans_qs.filter(
            Q(admission__student__student_id__icontains=query)
            | Q(admission__student__first_name__icontains=query)
            | Q(admission__student__last_name__icontains=query)
        )

    plans = list(plans_qs)
    admission_ids = [plan.admission_id for plan in plans]
    credit_map: dict[int, Decimal] = {}
    if admission_ids:
        credit_rows = (
            AdvanceCredit.objects.filter(
                admission_id__in=admission_ids,
                status=AdvanceCredit.Status.ACTIVE,
            )
            .order_by()
            .values("admission_id")
            .annotate(total=Sum("remaining_amount"))
        )
        for row in credit_rows:
            credit_map[row["admission_id"]] = row["total"] or ZERO

    rows: list[OutstandingAdmissionRow] = []
    agg_billed = ZERO
    agg_paid = ZERO
    agg_outstanding = ZERO
    agg_overdue = ZERO
    agg_not_yet_due = ZERO
    agg_undated = ZERO
    agg_credit = ZERO

    for plan in plans:
        billed = ZERO
        outstanding = ZERO
        overdue = ZERO
        not_yet_due = ZERO
        undated = ZERO

        for inst in plan.installments.all():
            allocated = _active_allocated_from_prefetched(inst)
            inst_out = max(inst.amount - allocated, ZERO)
            billed += inst.amount
            outstanding += inst_out
            if inst_out <= ZERO:
                continue
            eff_date = _effective_date_from_prefetched(inst)
            if eff_date is None:
                undated += inst_out
            elif eff_date < as_of:
                overdue += inst_out
            else:
                not_yet_due += inst_out

        paid = billed - outstanding
        cred = credit_map.get(plan.admission_id, ZERO)
        if only_outstanding and outstanding <= ZERO:
            continue

        rows.append(
            OutstandingAdmissionRow(
                admission=plan.admission,
                plan=plan,
                total_billed=billed,
                total_paid=paid,
                total_outstanding=outstanding,
                overdue_outstanding=overdue,
                not_yet_due_outstanding=not_yet_due,
                undated_outstanding=undated,
                available_credit=cred,
            )
        )
        agg_billed += billed
        agg_paid += paid
        agg_outstanding += outstanding
        agg_overdue += overdue
        agg_not_yet_due += not_yet_due
        agg_undated += undated
        agg_credit += cred

    return OutstandingReportData(
        as_of=as_of,
        rows=rows,
        total_billed=agg_billed,
        total_paid=agg_paid,
        total_outstanding=agg_outstanding,
        overdue_outstanding=agg_overdue,
        not_yet_due_outstanding=agg_not_yet_due,
        undated_outstanding=agg_undated,
        available_credit=agg_credit,
    )


def build_overdue_installments_report(
    user,
    *,
    as_of: date | None = None,
    centre_id: int | None = None,
    course_id: int | None = None,
    batch_id: int | None = None,
    query: str = "",
) -> tuple[date, list[OverdueInstallmentRow], Decimal]:
    """
    List all COURSE installments on APPROVED plans that are overdue as of ``as_of``.

    Stage-based charges (APPLICATION and ADMISSION) have no calendar due date
    and are never overdue.
    """
    as_of = parse_report_date(as_of)
    installments_qs = (
        FeeInstallment.objects.filter(
            category=FeeInstallment.Category.COURSE,
            fee_plan__status=FeePlan.Status.APPROVED,
            fee_plan__admission__centre__in=user.get_accessible_centres(),
        )
        .select_related(
            "fee_plan__admission__student",
            "fee_plan__admission__centre",
            "fee_plan__admission__course",
            "fee_plan__admission__batch",
        )
        .prefetch_related("due_date_revisions", "allocations")
        .order_by("original_due_date", "sequence", "pk")
    )
    if centre_id:
        installments_qs = installments_qs.filter(fee_plan__admission__centre_id=centre_id)
    if course_id:
        installments_qs = installments_qs.filter(fee_plan__admission__course_id=course_id)
    if batch_id:
        installments_qs = installments_qs.filter(fee_plan__admission__batch_id=batch_id)
    if query:
        installments_qs = installments_qs.filter(
            Q(fee_plan__admission__student__student_id__icontains=query)
            | Q(fee_plan__admission__student__first_name__icontains=query)
            | Q(fee_plan__admission__student__last_name__icontains=query)
        )

    rows: list[OverdueInstallmentRow] = []
    total_overdue = ZERO
    for inst in installments_qs:
        eff_date = _effective_date_from_prefetched(inst)
        if eff_date is None or eff_date >= as_of:
            continue
        allocated = _active_allocated_from_prefetched(inst)
        outstanding = max(inst.amount - allocated, ZERO)
        if outstanding <= ZERO:
            continue
        days_overdue = (as_of - eff_date).days
        rows.append(
            OverdueInstallmentRow(
                admission=inst.fee_plan.admission,
                installment=inst,
                original_due_date=inst.original_due_date or eff_date,
                effective_due_date=eff_date,
                days_overdue=days_overdue,
                billed=inst.amount,
                paid=allocated,
                overdue_amount=outstanding,
            )
        )
        total_overdue += outstanding

    rows.sort(
        key=lambda r: (r.effective_due_date, r.admission.student.first_name, r.installment.pk)
    )
    return as_of, rows, total_overdue


def build_collection_report(
    user,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    collection_centre_id: int | None = None,
    admission_centre_id: int | None = None,
    course_id: int | None = None,
    mode: str = "",
    collector_id: int | None = None,
    status: str = "",
    query: str = "",
) -> CollectionReportData:
    """
    Build the period-wise collection & receipt register.

    Explicitly separates collection-centre totals (`Payment.centre`) from
    admission-centre totals (`Payment.admission.centre`) and computes daily
    and payment-mode summaries using database aggregations.
    """
    accessible = user.get_accessible_centres()
    base_qs = (
        Payment.objects.filter(admission__centre__in=accessible)
        .select_related(
            "admission__student",
            "admission__centre",
            "admission__course",
            "admission__batch",
            "centre",
            "recorded_by",
            "receipt",
        )
        .order_by("-paid_on", "-posted_at", "-pk")
    )
    if date_from:
        base_qs = base_qs.filter(paid_on__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(paid_on__lte=date_to)
    if collection_centre_id:
        base_qs = base_qs.filter(centre_id=collection_centre_id)
    if admission_centre_id:
        base_qs = base_qs.filter(admission__centre_id=admission_centre_id)
    if course_id:
        base_qs = base_qs.filter(admission__course_id=course_id)
    if mode:
        base_qs = base_qs.filter(mode=mode)
    if collector_id:
        base_qs = base_qs.filter(recorded_by_id=collector_id)
    if status in dict(Payment.Status.choices):
        base_qs = base_qs.filter(status=status)
    if query:
        base_qs = base_qs.filter(
            Q(receipt__number__icontains=query)
            | Q(admission__student__student_id__icontains=query)
            | Q(admission__student__first_name__icontains=query)
            | Q(admission__student__last_name__icontains=query)
            | Q(reference__icontains=query)
        )

    def _aggregate_dimension(code_field: str, name_field: str) -> list[CollectionSummaryRow]:
        grouped = (
            base_qs.order_by()
            .values(code_field, name_field)
            .annotate(
                posted_count=Count("pk", filter=Q(status=Payment.Status.POSTED)),
                posted_amount=Sum("amount", filter=Q(status=Payment.Status.POSTED)),
                reversed_count=Count("pk", filter=Q(status=Payment.Status.REVERSED)),
                reversed_amount=Sum("amount", filter=Q(status=Payment.Status.REVERSED)),
            )
            .order_by(code_field)
        )
        return [
            CollectionSummaryRow(
                label=str(row[name_field]),
                code=str(row[code_field]),
                posted_count=row["posted_count"] or 0,
                posted_amount=row["posted_amount"] or ZERO,
                reversed_count=row["reversed_count"] or 0,
                reversed_amount=row["reversed_amount"] or ZERO,
            )
            for row in grouped
        ]

    by_collection = _aggregate_dimension("centre__code", "centre__name")
    by_admission = _aggregate_dimension("admission__centre__code", "admission__centre__name")

    daily_grouped = (
        base_qs.order_by()
        .values("paid_on")
        .annotate(
            posted_count=Count("pk", filter=Q(status=Payment.Status.POSTED)),
            posted_amount=Sum("amount", filter=Q(status=Payment.Status.POSTED)),
            reversed_count=Count("pk", filter=Q(status=Payment.Status.REVERSED)),
            reversed_amount=Sum("amount", filter=Q(status=Payment.Status.REVERSED)),
        )
        .order_by("-paid_on")
    )
    by_day = [
        CollectionSummaryRow(
            label=row["paid_on"].strftime("%d %b %Y"),
            code=row["paid_on"].isoformat(),
            posted_count=row["posted_count"] or 0,
            posted_amount=row["posted_amount"] or ZERO,
            reversed_count=row["reversed_count"] or 0,
            reversed_amount=row["reversed_amount"] or ZERO,
        )
        for row in daily_grouped
    ]

    mode_labels = dict(Payment._meta.get_field("mode").choices)
    mode_grouped = (
        base_qs.order_by()
        .values("mode")
        .annotate(
            posted_count=Count("pk", filter=Q(status=Payment.Status.POSTED)),
            posted_amount=Sum("amount", filter=Q(status=Payment.Status.POSTED)),
            reversed_count=Count("pk", filter=Q(status=Payment.Status.REVERSED)),
            reversed_amount=Sum("amount", filter=Q(status=Payment.Status.REVERSED)),
        )
        .order_by("mode")
    )
    by_mode = [
        CollectionSummaryRow(
            label=str(mode_labels.get(row["mode"], row["mode"])),
            code=str(row["mode"]),
            posted_count=row["posted_count"] or 0,
            posted_amount=row["posted_amount"] or ZERO,
            reversed_count=row["reversed_count"] or 0,
            reversed_amount=row["reversed_amount"] or ZERO,
        )
        for row in mode_grouped
    ]

    totals = base_qs.order_by().aggregate(
        posted_count=Count("pk", filter=Q(status=Payment.Status.POSTED)),
        posted_total=Sum("amount", filter=Q(status=Payment.Status.POSTED)),
        reversed_count=Count("pk", filter=Q(status=Payment.Status.REVERSED)),
        reversed_total=Sum("amount", filter=Q(status=Payment.Status.REVERSED)),
    )

    return CollectionReportData(
        payments=list(base_qs),
        by_collection_centre=by_collection,
        by_admission_centre=by_admission,
        by_day=by_day,
        by_mode=by_mode,
        posted_count=totals["posted_count"] or 0,
        posted_total=totals["posted_total"] or ZERO,
        reversed_count=totals["reversed_count"] or 0,
        reversed_total=totals["reversed_total"] or ZERO,
    )


def build_adjustments_report(
    user,
    *,
    centre_id: int | None = None,
    status: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, list]:
    """
    Fetch centre-scoped payment reversals, advance-credit refunds, and
    fee-plan reassignments for the Adjustments Report.
    """
    accessible = user.get_accessible_centres()
    reversals = PaymentReversal.objects.filter(
        payment__admission__centre__in=accessible
    ).select_related(
        "payment__receipt",
        "payment__admission__student",
        "payment__admission__centre",
        "payment__centre",
        "requested_by",
        "decided_by",
    )
    refunds = Refund.objects.filter(admission__centre__in=accessible).select_related(
        "admission__student",
        "admission__centre",
        "credit",
        "original_payment__receipt",
        "requested_by",
        "decided_by",
    )
    reassignments = FeePlanReassignment.objects.filter(
        admission__centre__in=accessible
    ).select_related(
        "admission__student",
        "admission__centre",
        "from_plan",
        "to_plan",
        "requested_by",
        "decided_by",
    )

    if centre_id:
        reversals = reversals.filter(payment__admission__centre_id=centre_id)
        refunds = refunds.filter(admission__centre_id=centre_id)
        reassignments = reassignments.filter(admission__centre_id=centre_id)
    if status in ("PENDING", "APPROVED", "REJECTED"):
        reversals = reversals.filter(status=status)
        refunds = refunds.filter(status=status)
        reassignments = reassignments.filter(status=status)
    if date_from:
        reversals = reversals.filter(requested_at__date__gte=date_from)
        refunds = refunds.filter(requested_at__date__gte=date_from)
        reassignments = reassignments.filter(requested_at__date__gte=date_from)
    if date_to:
        reversals = reversals.filter(requested_at__date__lte=date_to)
        refunds = refunds.filter(requested_at__date__lte=date_to)
        reassignments = reassignments.filter(requested_at__date__lte=date_to)

    return {
        "reversals": list(reversals.order_by("-requested_at", "-pk")),
        "refunds": list(refunds.order_by("-requested_at", "-pk")),
        "reassignments": list(reassignments.order_by("-requested_at", "-pk")),
    }


def audit_events_queryset(
    user,
    *,
    centre_id: int | None = None,
    event_type: str = "",
    actor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
):
    """Centre-scoped append-only financial audit events queryset."""
    queryset = (
        FinancialAuditEvent.objects.filter(admission__centre__in=user.get_accessible_centres())
        .select_related(
            "admission__student",
            "admission__centre",
            "actor",
            "payment__receipt",
            "installment",
        )
        .order_by("-created_at", "-pk")
    )
    if centre_id:
        queryset = queryset.filter(admission__centre_id=centre_id)
    if event_type in dict(FinancialAuditEvent.Event.choices):
        queryset = queryset.filter(event_type=event_type)
    if actor_id:
        queryset = queryset.filter(actor_id=actor_id)
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    if query:
        queryset = queryset.filter(
            Q(detail__icontains=query)
            | Q(admission__student__student_id__icontains=query)
            | Q(admission__student__first_name__icontains=query)
            | Q(admission__student__last_name__icontains=query)
            | Q(payment__receipt__number__icontains=query)
        )
    return queryset


def dashboard_summary(user) -> dict:
    """
    Build centre-scoped operational and financial KPIs for the dashboard.

    Financial figures are computed only when the user belongs to a financial or
    management role (``ADMIN``, ``MANAGEMENT``, ``ACCOUNTS``, or superuser).
    """
    today = timezone.localdate()
    month_start = today.replace(day=1)
    accessible = user.get_accessible_centres()

    admissions_qs = Admission.objects.for_user(user)
    counts = admissions_qs.order_by().aggregate(
        total=Count("pk"),
        enrolled=Count("pk", filter=Q(status=Admission.Status.ENROLLED)),
        enquired=Count("pk", filter=Q(status=Admission.Status.ENQUIRED)),
    )
    recent_admissions = list(
        admissions_qs.select_related("student", "centre", "course", "batch").order_by(
            "-enquired_at", "-pk"
        )[:6]
    )

    can_view_finance = bool(
        user.is_authenticated
        and (
            user.is_superuser or getattr(user, "role", None) in ("ADMIN", "MANAGEMENT", "ACCOUNTS")
        )
    )

    finance_kpis = {
        "can_view_finance": can_view_finance,
        "month_collection": ZERO,
        "month_receipt_count": 0,
        "total_outstanding": ZERO,
        "overdue_outstanding": ZERO,
        "available_credit": ZERO,
        "pending_due_date_count": 0,
        "pending_refund_count": 0,
        "pending_reversal_count": 0,
        "pending_reassignment_count": 0,
        "recent_payments": [],
    }

    if can_view_finance:
        month_stats = (
            Payment.objects.filter(
                admission__centre__in=accessible,
                status=Payment.Status.POSTED,
                paid_on__gte=month_start,
                paid_on__lte=today,
            )
            .order_by()
            .aggregate(total=Sum("amount"), count=Count("pk"))
        )
        outstanding_report = build_outstanding_report(user, as_of=today)
        finance_kpis.update(
            {
                "month_collection": month_stats["total"] or ZERO,
                "month_receipt_count": month_stats["count"] or 0,
                "total_outstanding": outstanding_report.total_outstanding,
                "overdue_outstanding": outstanding_report.overdue_outstanding,
                "available_credit": outstanding_report.available_credit,
                "pending_due_date_count": DueDateChangeRequest.objects.filter(
                    status=DueDateChangeRequest.Status.PENDING,
                    installment__fee_plan__status=FeePlan.Status.APPROVED,
                    installment__fee_plan__admission__centre__in=accessible,
                ).count(),
                "pending_refund_count": Refund.objects.filter(
                    status=Refund.Status.PENDING,
                    admission__centre__in=accessible,
                ).count(),
                "pending_reversal_count": PaymentReversal.objects.filter(
                    status=PaymentReversal.Status.PENDING,
                    payment__admission__centre__in=accessible,
                ).count(),
                "pending_reassignment_count": FeePlanReassignment.objects.filter(
                    status=FeePlanReassignment.Status.PENDING,
                    admission__centre__in=accessible,
                ).count(),
                "recent_payments": list(
                    Payment.objects.filter(admission__centre__in=accessible)
                    .select_related("admission__student", "admission__centre", "centre", "receipt")
                    .order_by("-paid_on", "-posted_at", "-pk")[:6]
                ),
            }
        )

    return {
        "today": today,
        "month_start": month_start,
        "accessible_centres": list(accessible),
        "total_admissions": counts["total"] or 0,
        "enrolled_admissions": counts["enrolled"] or 0,
        "enquired_admissions": counts["enquired"] or 0,
        "recent_admissions": recent_admissions,
        **finance_kpis,
    }

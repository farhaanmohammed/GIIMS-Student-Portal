from decimal import Decimal
from typing import Any, ClassVar

from django import forms

from apps.fees.models import FeeInstallment, FeePlan
from apps.fees.services import (
    FeeComponent,
    compute_gst_amounts,
    normalize_components,
    validate_components,
)

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-slate-900 "
    "shadow-xs focus:border-indigo-600 focus:outline-none focus:ring-1 "
    "focus:ring-indigo-600 sm:text-sm"
)
COURSE_COMPONENT_COUNT = 4


def _date_input() -> forms.DateInput:
    """Return a fresh date widget so fields never share a mutable widget."""
    return forms.DateInput(attrs={"class": INPUT_CLASSES, "type": "date"})


def _text_input() -> forms.TextInput:
    return forms.TextInput(attrs={"class": INPUT_CLASSES, "autocomplete": "off"})


def _money_input() -> forms.NumberInput:
    return forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0.01"})


class FeePlanForm(forms.Form):
    """
    Draft fee plan editor.

    The six components are fixed by policy: one application charge, one
    admission charge, and four course installments. The client may preview
    totals, but ``apps.fees.services`` re-derives and re-validates everything.
    """

    course_fee = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Taxable course fee (excl. GST)",
        widget=_money_input(),
    )
    gst_rate = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        max_value=Decimal("100.00"),
        initial=Decimal("18.00"),
        label="GST rate (%)",
        help_text="Snapshotted on this plan. There is no global or course-level rate.",
        widget=_money_input(),
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )

    application_description = forms.CharField(
        max_length=200,
        initial="Application Fee",
        label="Application charge description",
        widget=_text_input(),
    )
    application_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=Decimal("1000.00"),
        label="Application amount",
        widget=_money_input(),
    )

    admission_description = forms.CharField(
        max_length=200,
        initial="Admission Fee",
        label="Admission charge description",
        widget=_text_input(),
    )
    admission_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=Decimal("10000.00"),
        label="Admission amount",
        widget=_money_input(),
    )

    field_order: ClassVar[list[str]] = [
        "course_fee",
        "gst_rate",
        "notes",
        "application_description",
        "application_amount",
        "admission_description",
        "admission_amount",
    ]

    def __init__(self, *args, plan: FeePlan | None = None, **kwargs):
        initial = dict(kwargs.pop("initial", {}) or {})
        if plan is not None and not args:
            initial = {**self._initial_from_plan(plan), **initial}
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

        for index in range(1, COURSE_COMPONENT_COUNT + 1):
            description_name = f"course_{index}_description"
            amount_name = f"course_{index}_amount"
            due_date_name = f"course_{index}_due_date"
            self.fields[description_name] = forms.CharField(
                max_length=200,
                initial=f"Course Installment {index}",
                label=f"Course installment {index} description",
                widget=_text_input(),
            )
            self.fields[amount_name] = forms.DecimalField(
                max_digits=12,
                decimal_places=2,
                min_value=Decimal("0.01"),
                label=f"Course installment {index} amount",
                widget=_money_input(),
            )
            self.fields[due_date_name] = forms.DateField(
                label=f"Course installment {index} suggested due date",
                widget=_date_input(),
            )

    @staticmethod
    def _initial_from_plan(plan: FeePlan) -> dict[str, Any]:
        initial: dict[str, Any] = {
            "course_fee": plan.course_fee,
            "gst_rate": plan.gst_rate,
            "notes": plan.notes,
        }
        installments = sorted(plan.installments.all(), key=lambda row: row.sequence)
        stage_rows = {
            installment.category: installment
            for installment in installments
            if installment.category != FeeInstallment.Category.COURSE
        }
        for category, prefix in (
            (FeeInstallment.Category.APPLICATION, "application"),
            (FeeInstallment.Category.ADMISSION, "admission"),
        ):
            installment = stage_rows.get(category)
            if installment is not None:
                initial[f"{prefix}_description"] = installment.description
                initial[f"{prefix}_amount"] = installment.amount

        course_rows = [
            installment
            for installment in installments
            if installment.category == FeeInstallment.Category.COURSE
        ]
        for index, installment in enumerate(course_rows[:COURSE_COMPONENT_COUNT], start=1):
            initial[f"course_{index}_description"] = installment.description
            initial[f"course_{index}_amount"] = installment.amount
            initial[f"course_{index}_due_date"] = installment.original_due_date
        return initial

    def components(self) -> list[FeeComponent]:
        """Return the six submitted components as service input."""
        cleaned = self.cleaned_data
        rows = [
            FeeComponent(
                sequence=1,
                category=FeeInstallment.Category.APPLICATION,
                description=cleaned["application_description"],
                amount=cleaned["application_amount"],
                original_due_date=None,
            ),
            FeeComponent(
                sequence=2,
                category=FeeInstallment.Category.ADMISSION,
                description=cleaned["admission_description"],
                amount=cleaned["admission_amount"],
                original_due_date=None,
            ),
        ]
        for index in range(1, COURSE_COMPONENT_COUNT + 1):
            rows.append(
                FeeComponent(
                    sequence=index + 2,
                    category=FeeInstallment.Category.COURSE,
                    description=cleaned[f"course_{index}_description"],
                    amount=cleaned[f"course_{index}_amount"],
                    original_due_date=cleaned[f"course_{index}_due_date"],
                )
            )
        return rows

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        required = [
            "course_fee",
            "gst_rate",
            "application_description",
            "application_amount",
            "admission_description",
            "admission_amount",
        ]
        for index in range(1, COURSE_COMPONENT_COUNT + 1):
            required.extend(
                [
                    f"course_{index}_description",
                    f"course_{index}_amount",
                    f"course_{index}_due_date",
                ]
            )
        if any(name not in cleaned for name in required):
            return cleaned

        try:
            _, _, _, total_payable = compute_gst_amounts(cleaned["course_fee"], cleaned["gst_rate"])
            validate_components(normalize_components(self.components()), total_payable)
        except ValueError as exc:
            self.add_error(None, str(exc))
        return cleaned


class DueDateChangeForm(forms.Form):
    """Capture a proposed due date and the mandatory business reason."""

    proposed_due_date = forms.DateField(label="Proposed due date", widget=_date_input())
    reason = forms.CharField(
        min_length=10,
        label="Reason",
        help_text="At least 10 characters. Stored permanently in the audit trail.",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )


class DueDateDecisionForm(forms.Form):
    """Approve or reject a pending due-date change request."""

    decision = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject")),
        widget=forms.RadioSelect,
        label="Decision",
    )
    note = forms.CharField(
        required=False,
        label="Decision note",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
    )


class FeeReassignmentForm(forms.Form):
    """Raise a reassignment request with a mandatory reason."""

    reason = forms.CharField(
        min_length=10,
        label="Reason",
        help_text="At least 10 characters. Stored permanently in the audit trail.",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )
    request_key = forms.CharField(widget=forms.HiddenInput, required=False)


class FeeReassignmentDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject")),
        widget=forms.RadioSelect,
        label="Decision",
    )
    note = forms.CharField(
        required=False,
        label="Decision note",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
    )

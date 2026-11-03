from decimal import Decimal
from typing import Any, ClassVar

from django import forms
from django.utils import timezone

from apps.centres.models import Centre
from apps.fees.models import FeeInstallment, FeePlan
from apps.payments.models import AdvanceCredit, Payment, PaymentMode
from apps.payments.selectors import available_credit, fifo_suggestions, installment_outstanding

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-slate-900 "
    "shadow-xs focus:border-indigo-600 focus:outline-none focus:ring-1 "
    "focus:ring-indigo-600 sm:text-sm"
)
ZERO = Decimal("0.00")


def _date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"class": INPUT_CLASSES, "type": "date"})


def _text_input() -> forms.TextInput:
    return forms.TextInput(attrs={"class": INPUT_CLASSES, "autocomplete": "off"})


def _money_input() -> forms.NumberInput:
    return forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01", "min": "0.00"})


def _approved_plan(admission) -> FeePlan | None:
    return (
        FeePlan.objects.filter(admission=admission, status=FeePlan.Status.APPROVED)
        .prefetch_related("installments")
        .first()
    )


class PaymentForm(forms.Form):
    """
    Collect a payment and optionally split it across installments.

    Blank allocation fields mean "apply oldest-first automatically" (FIFO).
    ``retain_as_credit`` explicitly keeps the whole amount as advance credit.
    Totals are always re-derived by the service.
    """

    collection_centre = forms.ModelChoiceField(
        queryset=Centre.objects.none(),
        label="Collection centre",
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
        help_text="The centre that actually received this payment.",
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount received",
        widget=_money_input(),
    )
    paid_on = forms.DateField(label="Payment date", widget=_date_input())
    mode = forms.ChoiceField(label="Payment mode", choices=PaymentMode.choices)
    reference = forms.CharField(
        required=False,
        max_length=100,
        label="Reference",
        help_text="Cheque number, UPI reference, or bank transaction reference.",
        widget=_text_input(),
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
    )
    retain_as_credit = forms.BooleanField(
        required=False,
        label="Do not allocate now; retain the whole amount as advance credit",
    )
    request_key = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(
        self,
        *args,
        admission=None,
        user=None,
        suggestion_amount=None,
        prefill_allocations: bool = False,
        **kwargs,
    ):
        initial = dict(kwargs.pop("initial", {}) or {})
        initial.setdefault("paid_on", timezone.localdate())
        if suggestion_amount is not None:
            initial.setdefault("amount", suggestion_amount)
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

        centres = user.get_accessible_centres() if user is not None else Centre.objects.none()
        self.fields["collection_centre"].queryset = centres
        if user is not None and getattr(user, "primary_centre_id", None):
            self.fields["collection_centre"].initial = user.primary_centre_id

        self.admission = admission
        self.installments: list[FeeInstallment] = []
        self.suggested: dict[int, Decimal] = {}
        if admission is None:
            return

        plan = _approved_plan(admission)
        if plan is None:
            return

        suggestions = fifo_suggestions(
            admission,
            suggestion_amount if suggestion_amount is not None else plan.total_payable,
        )
        self.suggested = {row.installment.pk: row.suggested_amount for row in suggestions}
        for row in suggestions:
            installment = row.installment
            self.installments.append(installment)
            name = self.allocation_field_name(installment)
            field = forms.DecimalField(
                required=False,
                max_digits=12,
                decimal_places=2,
                min_value=ZERO,
                label=installment.description,
                widget=forms.NumberInput(
                    attrs={
                        "class": INPUT_CLASSES,
                        "step": "0.01",
                        "min": "0",
                        "max": str(row.outstanding),
                        "placeholder": "0.00",
                    }
                ),
            )
            self.fields[name] = field
            if prefill_allocations and row.suggested_amount > ZERO:
                self.fields[name].initial = row.suggested_amount

    @staticmethod
    def allocation_field_name(installment: FeeInstallment) -> str:
        return f"allocation_{installment.pk}"

    def allocation_fields(self) -> list[dict[str, Any]]:
        """Template helper: the rendered field plus its context per installment."""
        rows = []
        for installment in self.installments:
            name = self.allocation_field_name(installment)
            rows.append(
                {
                    "installment": installment,
                    "field": self[name],
                    "suggested": self.suggested.get(installment.pk, ZERO),
                    "outstanding": installment_outstanding(installment),
                }
            )
        return rows

    def allocation_rows(self) -> list[dict[str, Any]]:
        rows = []
        for installment in self.installments:
            value = self.cleaned_data.get(self.allocation_field_name(installment)) or ZERO
            if value > ZERO:
                rows.append({"installment": installment, "amount": value})
        return rows

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        amount = cleaned.get("amount")
        rows = self.allocation_rows()
        allocated = sum((row["amount"] for row in rows), ZERO)
        if amount is not None and allocated > amount:
            self.add_error(None, "Allocations cannot exceed the amount received.")
        if cleaned.get("retain_as_credit") and allocated > ZERO:
            self.add_error(
                None,
                "Clear the allocation amounts if you want to retain the whole payment as credit.",
            )
        for row in rows:
            installment = row["installment"]
            if row["amount"] > installment_outstanding(installment):
                self.add_error(
                    None,
                    f"{installment.description} only has "
                    f"{installment_outstanding(installment)} outstanding.",
                )
        return cleaned

    def allocations_argument(self):
        """``None`` triggers FIFO; an empty list means "keep as credit"."""
        if self.cleaned_data.get("retain_as_credit"):
            return []
        rows = self.allocation_rows()
        return rows or None


class CreditApplicationForm(forms.Form):
    """Apply available advance credit to one installment."""

    installment = forms.ModelChoiceField(
        queryset=FeeInstallment.objects.none(),
        label="Installment",
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Credit to apply",
        widget=_money_input(),
    )
    reason = forms.CharField(
        required=False,
        label="Note",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 2}),
    )
    request_key = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, admission=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.admission = admission
        self.credit_available = ZERO
        if admission is None:
            return
        plan = _approved_plan(admission)
        if plan is None:
            self.fields["installment"].queryset = FeeInstallment.objects.none()
            return
        installments = [
            installment
            for installment in plan.installments.all()
            if installment_outstanding(installment) > ZERO
        ]
        self.fields["installment"].queryset = FeeInstallment.objects.filter(
            pk__in=[installment.pk for installment in installments]
        )
        self.credit_available = available_credit(admission)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        installment = cleaned.get("installment")
        amount = cleaned.get("amount")
        if installment is None or amount is None:
            return cleaned
        if amount > installment_outstanding(installment):
            self.add_error(None, "The credit exceeds this installment's outstanding balance.")
        if self.admission is not None and amount > available_credit(self.admission):
            self.add_error(None, "The credit exceeds the available advance credit.")
        return cleaned


class RefundForm(forms.Form):
    """Raise a refund request against one credit lot."""

    credit = forms.ModelChoiceField(
        queryset=AdvanceCredit.objects.none(),
        label="Credit lot",
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Refund amount",
        widget=_money_input(),
    )
    reason = forms.CharField(
        min_length=10,
        label="Reason",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )
    mode = forms.ChoiceField(label="Refund mode", choices=PaymentMode.choices)
    request_key = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, admission=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.admission = admission
        if admission is None:
            return
        self.fields["credit"].queryset = AdvanceCredit.objects.filter(
            admission=admission,
            status=AdvanceCredit.Status.ACTIVE,
            remaining_amount__gt=ZERO,
        ).select_related("source_payment")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        credit = cleaned.get("credit")
        amount = cleaned.get("amount")
        if credit is not None and amount is not None and amount > credit.remaining_amount:
            self.add_error(None, "The refund exceeds the available refundable credit.")
        return cleaned


class DecisionForm(forms.Form):
    """Shared approve/reject decision with a note and optional effective date."""

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

    field_order: ClassVar[list[str]] = ["decision", "note"]


class RefundDecisionForm(DecisionForm):
    refunded_on = forms.DateField(
        required=False,
        label="Actual refund date",
        widget=_date_input(),
        help_text="Defaults to today when approved.",
    )


class PaymentReversalForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        label="Reason",
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )
    request_key = forms.CharField(widget=forms.HiddenInput, required=False)


class PaymentFilterForm(forms.Form):
    """Whitelisted filters for the payment register."""

    q = forms.CharField(required=False, max_length=100)
    mode = forms.ChoiceField(
        required=False,
        choices=(("", "All modes"), *PaymentMode.choices),
    )
    centre = forms.ModelChoiceField(
        required=False,
        queryset=Centre.objects.none(),
        empty_label="All accessible centres",
    )
    status = forms.ChoiceField(
        required=False,
        choices=(("", "All statuses"), *Payment.Status.choices),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["centre"].queryset = user.get_accessible_centres()


class RefundFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)

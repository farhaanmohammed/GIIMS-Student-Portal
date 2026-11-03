"""Synthetic factories for payment tests. No real personal or financial data."""

from datetime import date
from decimal import Decimal
from typing import Any

from apps.fees.tests.factories import FeeTestBase
from apps.payments.models import AdvanceCredit, Payment, PaymentMode
from apps.payments.services import record_payment

ZERO = Decimal("0.00")


class PaymentTestBase(FeeTestBase):
    """Fee masters plus a posted payment helper."""

    def make_payment(
        self,
        *,
        admission=None,
        amount=Decimal("1000.00"),
        paid_on: date | None = None,
        mode: str = PaymentMode.CASH,
        collection_centre=None,
        actor=None,
        allocations="fifo",
        request_key: str | None = None,
        **overrides: Any,
    ) -> Payment:
        admission = admission or self.make_admission()
        kwargs: dict[str, Any] = {
            "amount": amount,
            "paid_on": paid_on or date(2026, 7, 15),
            "mode": mode,
            "collection_centre": collection_centre or admission.centre,
            "actor": actor or self.accounts,
            "allocations": None if allocations == "fifo" else allocations,
            "request_key": request_key,
        }
        kwargs.update(overrides)
        return record_payment(admission, **kwargs)

    def credit_lot(self, payment: Payment) -> AdvanceCredit:
        return AdvanceCredit.objects.get(source_payment=payment)

    def course_installments(self, plan):
        return list(plan.installments.filter(category="COURSE").order_by("sequence"))

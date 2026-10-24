---
trigger: model_decision
description: Mandatory GIIMS financial rules when changing fee plans, installments, payments, allocations, receipts, discounts, refunds, reversals, or fee reports.
---

# GIIMS Financial Workflow Best Practices

## Source of truth
- Keep financial operations in explicit, tested service functions such as `record_payment()`, `allocate_payment()`, and `reverse_payment()`.
- Use `Decimal` and Django `DecimalField` for all monetary values.
- Recalculate charges, approved adjustments, allocations, and outstanding amounts on the server; never trust browser-submitted totals.
- Use a documented operating currency and rounding policy.
- Preserve the agreed fee plan for each admission even when course defaults change.

## Posting a payment
- Validate the actor's permissions and access to the admission/centre.
- Validate amount, payment method, payment date, currency, and required reference fields.
- Use `transaction.atomic()` for the complete financial write.
- Use durable idempotency protection or an equivalent uniqueness design against duplicate submissions/retries.
- Lock relevant rows when concurrency could create duplicate allocation or inconsistent balances.
- Create payment, allocations, receipt metadata, and audit event consistently.
- Roll back the whole operation if any required step fails.
- Do not let a payment allocate more than the available amount, unless an explicitly designed advance/credit policy permits it.

## Receipts
- Generate receipt numbers on the server.
- Confirm the number scope with GIIMS: global, per centre, or per financial year.
- Enforce uniqueness in PostgreSQL and use a concurrency-safe sequence/numbering design.
- Never use `COUNT(*) + 1` or unsafe `MAX(number) + 1`.
- Reprinting a receipt must not create another payment or new receipt number.
- Voided receipts remain traceable and must not silently disappear.

## Corrections, refunds, and discounts
- Never hard-delete a posted payment or silently change its financial effect.
- Use an authorized reversal/void/refund workflow with actor, timestamp, reason, and audit history.
- Treat discounts, fee waivers, credit adjustments, refunds, and payment reversals as distinct events.
- Define approval limits and approver requirements before implementation.
- Do not invent policy for discounts, refunds, receipt formats, or who can approve them.

## Allocations and reports
- Confirm whether partial payments allocate oldest-due-first or to an installment selected by an authorized user.
- Represent unallocated/advance funds explicitly if supported; never silently discard excess amounts.
- Define one consistent balance formula and reuse it across student ledger, dashboard, and exports.
- Distinguish total outstanding, overdue outstanding, and not-yet-due amounts.
- Calculate overdue status relative to installment due date and the report's selected as-of date.
- Apply the same permissions and centre filters to on-screen reports and exports.
- Test partial payments, exact payments, multiple installments, duplicate requests, reversals, overpayments, and date boundaries.

# Phase 5 — Payments, Allocations, Receipts, Credit, Refunds & Reversals

Canonical design and implementation record for Phase 5. Where this document and an
earlier plan disagree, this document wins for the payment domain.

## 1. Scope and confirmed decisions

| Decision | Rule implemented |
|---|---|
| A5 allocation | Staff-selected allocation with a FIFO **suggestion**. Partial payments and multi-installment payments are allowed. An allocation can never exceed an installment's outstanding amount. Excess money becomes advance credit. |
| Receipt numbering | `GIIMS/{CENTRE_CODE}/{FINANCIAL_YEAR}/{SEQUENCE}`, one independent sequence per `(centre, financial year)`, six-digit zero padding, generated only on the server. |
| A3 fee plans | `ADMIN`/`MANAGEMENT` create, edit, approve, activate and archive. `ACCOUNTS` create and edit drafts and create new versions but cannot approve or cancel. `ADMISSIONS` are read-only. |
| A4 reassignment | `ACCOUNTS` may request; only `ADMIN`/`MANAGEMENT` may approve. Reason is mandatory. Original assignment, payments, receipts and allocations are preserved through a reconciliation, never a silent rewrite. |
| Advance credit | A student-level credit ledger with lots, applications and refunds. Credit is distinct from outstanding fees and can never be applied twice. |
| Refunds | `ACCOUNTS` requests; `ADMIN`/`MANAGEMENT` approves. Drawn from a specific credit lot and capped by its remaining amount. |
| Reversals | `ACCOUNTS` requests; `ADMIN`/`MANAGEMENT` authorises. The payment, receipt and allocation rows are retained; state is compensated, never deleted. |
| Multi-centre | `Payment.centre` is the **collection** centre and may differ from `admission.centre`. Every query is centre-scoped server-side. |

## 2. Money and rounding

- All monetary columns are `DecimalField(max_digits=12, decimal_places=2)`.
- Services quantize every input with `ROUND_HALF_UP` to two places (`MONEY_QUANTUM`).
- Operating currency is INR and is stored on `Payment.currency` (default `INR`).
- No service trusts a client-submitted total, receipt number, outstanding balance or
  allocation sum. Totals are re-derived from rows inside the transaction.

## 3. Models (`apps.payments`)

| Model | Purpose | Key constraints / indexes |
|---|---|---|
| `ReceiptSequence` | Counter per `(centre, financial_year)` | unique `(centre, financial_year)`; `last_sequence >= 0` |
| `Payment` | One collection for an admission; `centre` is the collection centre | `amount > 0`; unique `request_key`; indexes on `(centre, paid_on)` and `(admission, paid_on)` |
| `Receipt` | One receipt per payment, retained forever | unique `number`; unique `(centre, financial_year, sequence)`; `sequence > 0` |
| `AdvanceCredit` | Credit lot from an overpayment or a reassignment | `original_amount > 0`; `0 <= remaining_amount <= original_amount`; source/payment link consistency; one `OVERPAYMENT` lot per payment |
| `InstallmentAllocation` | One explicit amount on one installment, from a payment or a credit lot | `amount > 0`; source/link consistency; ACTIVE has no reversal metadata and REVERSED requires it; unique `(request_key, credit)` for credit applications |
| `Refund` | Refund request/decision against one credit lot | `amount > 0`; pending has no decision metadata; decided requires actor/time; approved requires `refunded_on`; unique `request_key` |
| `RefundCreditAllocation` | Append-only record of the lot(s) a refund consumed | `amount > 0`; unique `(refund, credit)` |
| `PaymentReversal` | One reversal workflow per payment | one-to-one with `Payment`; `amount > 0`; decision metadata; unique `request_key` |
| `FinancialAuditEvent` | Append-only event log | `save()` on an existing row and `delete()` raise |

`InstallmentAllocation` is deliberately separate from `Payment`, so partial
payments, one payment across several installments, and advance-credit applications
are all traceable rows. Rows are never hard-deleted; reversal flips `status` and
records `reversed_by`, `reversed_at` and `reversal_reason`.

## 4. Receipt numbering

```text
GIIMS/{CENTRE_CODE}/{FINANCIAL_YEAR}/{SEQUENCE}
GIIMS/KOC/FY26-27/000001
GIIMS/TVM/FY26-27/000001
```

- The financial year is the Indian year (1 April – 31 March) derived from
  `Payment.paid_on`, not from the posting timestamp.
- `_next_receipt_sequence()` locks the `(centre, financial_year)` row with
  `select_for_update()` for the whole payment transaction. The first use of a
  centre/year creates the row in a savepoint; a concurrent creator loses the
  `IntegrityError` race and re-reads the locked row.
- Uniqueness is enforced twice: by the counter lock and by the database unique
  constraints on `Receipt.number` and `(centre, financial_year, sequence)`.
- Numbers are never reused. A reversed receipt keeps its number and becomes
  `REVERSED`; a cancelled receipt would be `CANCELLED`. There is no edit path.

## 5. Payment collection and allocation

`record_payment(admission, amount, paid_on, mode, collection_centre, actor, allocations=None, request_key=None)`

1. Authorise the actor (`ADMIN`/`MANAGEMENT`/`ACCOUNTS`) and both the admission's
   centre and the collection centre.
2. Lock the admission, the approved plan and its installments.
3. Create the `Payment` and the `Receipt`, and write `PAYMENT_RECORDED` and
   `RECEIPT_ISSUED` audit events.
4. Allocation:
   - `allocations=None` → FIFO suggestion: oldest `sequence` first, capped at each
     installment's outstanding amount.
   - Explicit list → honour the staff choice exactly, reject any amount above an
     installment's outstanding balance and reject a total above the payment.
   - `allocations=[]` → allocate nothing; the whole amount becomes credit.
5. Any unallocated remainder becomes one `AdvanceCredit` lot of source
   `OVERPAYMENT`, linked to the payment.

`installment_outstanding` is always `amount - sum(active allocations)`, clamped at
zero. There is no stored balance that can drift or be edited.

## 6. Advance credit

`AdvanceCredit` is a lot, not a single balance, so provenance survives:

- `OVERPAYMENT` lots point to the payment that created them (one lot per payment).
- `REASSIGNMENT` lots represent money released by an approved fee-plan
  reassignment; they intentionally have no single source payment.
- `remaining_amount` is stored for locking/reporting and is always derivable with
  `credit_remaining_from_history()` from active applications plus refunds.

`apply_advance_credit(installment, amount, actor, request_key)`:

- Locks the admission, the target installment and the credit lots.
- Consumes lots oldest-first.
- Rejects applications above the installment's outstanding balance or above the
  available credit.
- Writes one `InstallmentAllocation` per lot consumed and a `CREDIT_APPLIED`
  audit event.
- The same `request_key` returns the existing allocation instead of applying
  again; `(request_key, credit)` is unique at the database level.

## 7. Refunds

`request_refund(credit, amount, reason, mode, actor, request_key)`:

- Requires `ADMIN`/`MANAGEMENT`/`ACCOUNTS`, centre access, a reason of at least 10
  characters and a valid mode.
- Capped by `credit.remaining_amount`; rejects a `REVERSED` lot.
- Stores `original_payment` from the lot when the lot came from an overpayment.
- Does **not** reduce credit yet; approval is the only point that moves money.

`decide_refund(refund, approve, actor, note, refunded_on)`:

- Only `ADMIN`/`MANAGEMENT`; only a `PENDING` refund.
- Approval re-checks the lot, decrements `remaining_amount`, writes a
  `RefundCreditAllocation`, records the approver, timestamp and actual refund date,
  and writes an audit event.
- Rejection records the decision and leaves credit untouched.
- The original payment and receipt are never changed or deleted.

## 8. Payment reversals

`request_payment_reversal(payment, reason, actor, request_key)`:

- Requires a posted payment, a 10-character reason, centre access and no existing
  reversal (enforced by the one-to-one row and a pre-check).
- Blocked while a pending/approved refund exists against the payment's credit, and
  while an approved fee-plan reassignment exists for the admission (the money has
  already been reconciled through the reassignment path).
- The same request key is idempotent.

`decide_payment_reversal(reversal, approve, actor, note)`:

- Only `ADMIN`/`MANAGEMENT`; only `PENDING`.
- On approval, inside one transaction:
  1. reverse every active direct payment allocation (status + actor + reason);
  2. reverse every active allocation funded by the payment's credit lot;
  3. zero and mark the credit lot `REVERSED`;
  4. mark the receipt `REVERSED` (number unchanged);
  5. mark the payment `REVERSED`;
  6. write a `REVERSAL_APPROVED` audit event.
- Nothing is deleted. Duplicate reversal is impossible and a second decision is
  rejected.

## 9. A3 — draft fee-plan permissions

`apps/fees/services.py` splits:

- `PLAN_EDITOR_ROLES = {ADMIN, MANAGEMENT, ACCOUNTS}` — create, edit and version
  drafts.
- `PLAN_APPROVER_ROLES = {ADMIN, MANAGEMENT}` — approve and cancel.

Approved plans stay immutable; corrections still create a new version that
`supersedes` the old one, and the old version remains `SUPERSEDED`. `ACCOUNTS`
cannot approve its own draft because approval requires an approver role.

## 10. A4 — fee-plan reassignment with reconciliation

`FeePlanReassignment` snapshots `from_plan`, `to_plan`, `previous_total`,
`proposed_total`, `difference`, `had_payments`, the mandatory `reason`, requestor
and approver.

Request path (`request_fee_plan_reassignment`):
- `ADMIN`/`MANAGEMENT`/`ACCOUNTS`, centre-scoped.
- The target must be a newer unapproved `DRAFT`; the source is the current
  `APPROVED` plan.
- One pending request per admission (partial unique constraint).
- Difference must equal `proposed_total - previous_total` (database check).

Approval path (`decide_fee_plan_reassignment`, `ADMIN`/`MANAGEMENT` only):
1. Reverse every active allocation on the old plan (rows retained with reason).
2. Credit-funded allocations return their amount to their source lot.
3. Payment-funded allocations are preserved as one new `REASSIGNMENT` credit lot.
4. Approve the new plan version (the old one becomes `SUPERSEDED`).
5. Apply available credit FIFO to the new installments.
6. Record the audit event and decision metadata.

Payments and receipts are untouched, no installment amount is rewritten, and the
sum of money is conserved: it moves from old-plan allocations into a credit lot and
then into new-plan allocations.

## 11. Permission matrix

| Action | ADMIN | MANAGEMENT | ACCOUNTS | ADMISSIONS |
|---|---|---|---|---|
| Record payment / issue receipt | Yes | Yes | Yes | No |
| Apply advance credit | Yes | Yes | Yes | No |
| Request refund | Yes | Yes | Yes | No |
| Approve / reject refund | Yes | Yes | No | No |
| Request payment reversal | Yes | Yes | Yes | No |
| Authorise payment reversal | Yes | Yes | No | No |
| Create / edit draft fee plan | Yes | Yes | Yes | No |
| Create new plan version | Yes | Yes | Yes | No |
| Approve / cancel fee plan | Yes | Yes | No | No |
| Request reassignment | Yes | Yes | Yes | No |
| Approve / reject reassignment | Yes | Yes | No | No |
| View plans, payments and balances | Yes | Yes | Yes | Read-only plans |

All access is also centre-scoped: `ADMIN`, `MANAGEMENT` and explicitly flagged
users see all active centres; everyone else sees only their assigned centres.
Hiding a button is not authorization; every service and selector re-checks roles
and centre access on the server.

## 12. Multi-centre semantics

- `Payment.centre` = centre that physically collected the money.
- `Payment.admission.centre` = centre the student belongs to.
- They may differ; reports must show both and must not conflate them.
- Receipt sequences are per collection centre.
- `payments_for_user`, `credit_lots_for_user`, `pending_refunds` and
  `pending_reversals` scope through `admission__centre` and accept the actor's
  accessible centres.

## 13. Audit trail

Every service writes `FinancialAuditEvent` rows for: payment recorded, receipt
issued, allocation created, credit created/applied, refund requested/approved/
rejected, reversal requested/approved/rejected, reassignment requested/approved/
rejected. The model refuses updates and deletes, so the log is append-only.

## 14. Migrations

- `apps/payments/migrations/0001_initial.py` — all nine payment-domain models.
- `apps/fees/migrations/0002_feeplanreassignment.py` — the reassignment workflow
  table.
- Both are additive. No existing column, row, fee plan or installment is altered or
  removed. Apply with:

```powershell
uv run python manage.py migrate
```

Rollback for local development only:

```powershell
uv run python manage.py migrate payments zero
uv run python manage.py migrate fees 0001
```

## 15. Recorded assumptions and remaining business decisions

Management confirmation is still required for:

1. **Payment modes.** Implemented as `CASH`, `UPI`, `CARD`, `BANK_TRANSFER`,
   `CHEQUE`, `DEMAND_DRAFT`, `OTHER`. Confirm the institution's canonical list and
   whether cheque/DD clearance should be a separate state.
2. **Refund source.** Refunds are drawn only from available advance-credit lots.
   Confirm whether a refund may ever reverse an allocated installment payment
   directly (that currently requires a payment reversal instead).
3. **Reversal vs reassignment.** A payment reversal is blocked for any admission
   with an approved reassignment. Confirm whether targeted reversals should be
   permitted after reconciliation and, if so, how the reassignment credit lot is
   unwound.
4. **Self-approval.** The role rules prevent `ACCOUNTS` from approving, but
   `ADMIN`/`MANAGEMENT` may approve their own request because the policy does not
   forbid it. Confirm whether four-eyes approval is required.
5. **Refund settlement.** Approval records an actual refund date and mode, but no
   bank/payment-gateway integration or settlement reference is stored. Confirm
   whether a transaction reference and reconciliation state are needed.
6. **Cheque/DD lifecycle.** Cheques are posted immediately with a reference. Confirm
   whether they should remain `PENDING_CLEARANCE` and only allocate credit after
   clearing.
7. **Receipt cancellation.** `Receipt.Status.CANCELLED` exists but no service uses
   it; only reversal is implemented. Confirm whether a separate cancellation workflow
   is required.
8. **Receipt printing.** Receipts are records and screens only; no PDF/print layout
   or reprint audit is implemented yet.
9. **Write-off / adjustment.** No discount, waiver, write-off, penalty or interest
   logic exists and none was invented in Phase 5.

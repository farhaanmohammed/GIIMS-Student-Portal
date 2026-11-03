# GIIMS Logical Data Model

This document mixes the implemented schema with entities that are still logical. Confirm the open business decisions in `PROJECT_SPEC.md` before finalizing field names and constraints for planned entities.

## Implemented — Phase 1 foundations

- **User**: custom Django user model (`AbstractUser`) with `role`, `primary_centre`, a `centres` many-to-many access scope, and a `can_access_all_centres` flag.
- **Centre**: centre code, name, address, email, phone, and active status.
- **CentreMembership**: not modelled. Centre access is held directly on `User` (`primary_centre` plus `centres`).

## Implemented — Phase 2 Academic Masters

### AcademicYear (portal-wide, `apps.academics`)
- `label`: unique free text, max 20 characters, stripped on save. No separator or year-format convention is enforced.
- `start_date`, `end_date`: required dates; check constraint `academic_year_end_after_start` requires `end_date > start_date`.
- `is_active`: boolean, default `False`, indexed. Marks the year as open for new enrolments; more than one year may be active because no constraint prevents it.
- `is_current`: derived property, true when today falls within the date range. Nothing is stored.
- `created_at`, `updated_at` from `TimeStampedModel`.

### Course (portal-wide, `apps.academics`)
- `code`: unique, max 20 characters, validated against `^[A-Z0-9][A-Z0-9-]*$`; normalized to uppercase in `save()` and `clean_fields()`.
- `name`: max 150 characters.
- `duration_months`: positive small integer, 1–120, default 12.
- `description`: optional text.
- `is_active`: boolean, default `True`, indexed.
- No fee, pricing, or discount fields. Fee templates are deliberately deferred to Phase 4.

### Batch (centre-scoped, `apps.academics`)
- `centre`: foreign key to `centres.Centre`, `on_delete=PROTECT`, indexed; inherited from `CentreScopedModel`, so `Batch.objects.for_user(user)` filters rows by accessible centre.
- `course`: foreign key to `Course`, `on_delete=PROTECT`.
- `academic_year`: foreign key to `AcademicYear`, `on_delete=PROTECT`.
- `code`: max 30 characters, normalized to uppercase; unique per centre, course, and academic year via `batch_unique_code_per_centre_course_year`. The code format itself is free text.
- `start_date`, `end_date`: required dates; check constraint `batch_end_after_start`. Batch dates are not constrained to fall inside the academic year.
- `capacity`: positive integer, nullable with no default. `NULL` means the centre has not specified a capacity; check constraint `batch_capacity_positive` allows null or at least 1.
- `is_active`: boolean, default `True`, indexed.
- No derived seat counters or enrolment totals are stored. Capacity enforcement belongs to Phase 3 admissions.

### Scoping summary
- `AcademicYear` and `Course` are portal-wide master data: every authenticated role can read them, and writes are restricted to `ADMIN` and `MANAGEMENT`.
- `Batch` is the first centre-scoped domain entity: reads and writes go through `Batch.objects.for_user()`, and centre choices in the batch form come from `User.get_accessible_centres()`.

## Implemented — Phase 3 Students & Admissions

### Student (durable identity, `apps.students`)
- `student_id`: unique, indexed, nullable. Assigned once at enrollment and never changed; format `{centre code}-{batch code}-{course code}_{NNN}` (e.g. `TVM-ASP-AD_001`), zero-padded to at least three digits.
- Identity and contact fields: `first_name`, `last_name`, `middle_name`, `date_of_birth`, `gender` (`MALE`/`FEMALE`/`OTHER`), `phone`, `email`.
- Address fields: `address_line1`, `address_line2`, `city`, `state`, `pincode`.
- Guardian fields: `guardian_name`, `guardian_phone`, `guardian_relation`.
- `created_at`, `updated_at` from `TimeStampedModel`.
- Access is derived from the student's admissions; there is no direct centre foreign key on `Student`.

### StudentIdSequence (counter, `apps.students`)
- `prefix`: unique, max 60 chars, the ID prefix without the numeric suffix (e.g. `TVM-ASP-AD`).
- `last_number`: `PositiveIntegerField` with check constraint `student_id_sequence_non_negative`.
- One row per prefix. The row is locked with `select_for_update()` during allocation so concurrent enrollments cannot share a number. Because the prefix contains the batch code, the sequence restarts each year without storing a year column.

### Admission (centre-scoped, `apps.admissions`)
- Extends `CentreScopedModel`: `centre` is a protecting foreign key and `Admission.objects.for_user(user)` filters by accessible centre.
- `student` (PROTECT), `course` (PROTECT), `batch` (PROTECT), `academic_year` (PROTECT).
- `status`: `ENQUIRED` (default), `ENROLLED`, `DROPPED`, `COMPLETED`; indexed.
- `enquired_at` (auto), `enrolled_at` (nullable), `enrolled_by` (nullable user, PROTECT).
- Partial unique constraint `admission_unique_student_batch` on `(student, batch)` limited to `ENQUIRED`/`ENROLLED`, so a student cannot hold two active admissions to the same batch while historical admissions remain allowed.
- No admission number column: `Student.student_id` is the only identifier.

## Phase 4 — Fee Plans & Installments (implemented, `apps.fees`)

The canonical contract is `docs/PHASE4_FEE_PLANS.md`; this section summarises it and
must not diverge from it. The code lives in `apps.fees` (label `fees`), mounts at
`/fees/`, and is migrated by `apps/fees/migrations/0001_initial.py`.

### FeePlan (`apps.fees`)
- `admission`: FK `admissions.Admission`, `on_delete=PROTECT`,
  `related_name="fee_plans"`; required.
- `version`: `PositiveIntegerField`, default `1`; unique per admission.
- `status`: `DRAFT` (default), `APPROVED`, `SUPERSEDED`, `CANCELLED`; indexed.
- `supersedes`: self-FK to `FeePlan`, `on_delete=PROTECT`, nullable,
  `related_name="superseded_by"`; records correction lineage.
- `currency`: `CharField(3)`, default `"INR"`.
- `course_fee`: `DecimalField(12,2)`, non-negative; taxable value, GST-exclusive.
- `gst_rate`: `DecimalField(5,2)`, percentage, `0`–`100` inclusive. Snapshotted **per
  plan** (the July 2026 Logistics & SCM plan uses `18.00`). There is no global GST
  setting and no automatic course default, and the snapshot is immutable after
  approval.
- `gst_amount`: `DecimalField(12,2)`, computed by the service from `course_fee` and
  `gst_rate`; never user input.
- `total_payable`: `DecimalField(12,2)`, computed as `course_fee + gst_amount`.
- `notes`: optional `TextField`.
- `approved_at`: nullable datetime, set by the approval service.
- `approved_by`: FK `accounts.User`, `on_delete=PROTECT`, nullable, set by the
  approval service.
- `created_at`, `updated_at` from `TimeStampedModel`.

Status semantics:
- `DRAFT`: editable; not yet a student obligation.
- `APPROVED`: the admission's binding fee structure. At most one per admission.
- `SUPERSEDED`: an older approved version retained for audit after a correction. Never
  deleted and never edited.
- `CANCELLED`: a draft abandoned before approval.

Constraints:
- `fee_plan_course_fee_non_negative`: `course_fee >= 0`.
- `fee_plan_gst_amount_non_negative`: `gst_amount >= 0`.
- `fee_plan_total_payable_non_negative`: `total_payable >= 0`.
- `fee_plan_gst_rate_range`: `0 <= gst_rate <= 100`.
- `fee_plan_total_matches_components`: `total_payable = course_fee + gst_amount`.
- `fee_plan_approved_requires_metadata`: `status` in `("DRAFT", "CANCELLED")`, or both
  `approved_at` and `approved_by` are set. A draft cancelled before approval has no
  approver, so both statuses are exempt; `APPROVED` and `SUPERSEDED` always carry
  approval metadata.
- `fee_plan_unique_version_per_admission`: unique on `(admission, version)`.
- `fee_plan_one_approved_per_admission`: partial unique constraint on `admission`
  limited to `status = "APPROVED"`.

Additional model rules:
- No own `centre` column, matching `Student`. Plans are scoped through
  `admission__centre__in=user.get_accessible_centres()`, so a cross-centre plan
  returns 404 rather than leaking existence.
- No discount, waiver, or concession fields exist, by policy.

### FeeInstallment (`apps.fees`)
- `fee_plan`: FK `FeePlan`, `on_delete=PROTECT`, `related_name="installments"`;
  required.
- `sequence`: `PositiveSmallIntegerField`; unique per plan.
- `category`: `APPLICATION`, `ADMISSION`, or `COURSE`. For `APPLICATION` and
  `ADMISSION` the category is the **stage** at which the charge arises; both are
  stage-based, never calendar-based, and never overdue.
- `description`: `CharField(200)`, required.
- `amount`: `DecimalField(12,2)`, strictly greater than `0`.
- `original_due_date`: `DateField`, nullable. Required for `COURSE` and must be null
  for `APPLICATION`/`ADMISSION`.
- Constraints:
  - `fee_installment_unique_sequence`: unique on `(fee_plan, sequence)`.
  - `fee_installment_amount_positive`: `amount > 0`.
  - `fee_installment_due_date_matches_category`: enforces both directions, so
    `COURSE` requires `original_due_date IS NOT NULL` while `APPLICATION` and
    `ADMISSION` require `original_due_date IS NULL`.
- `effective_due_date` and overdue status are derived by selectors against an explicit
  `as_of` date; neither is stored.

Component-sum note: the six components (one `APPLICATION`, one `ADMISSION`, four
`COURSE`) must sum to `FeePlan.total_payable`. That sum is service-enforced, not a
database constraint. The application (INR 1,000) and admission (INR 10,000) charges are
inside the total, not additional to it.

### InstallmentDueDateRevision (append-only audit, `apps.fees`)
- `installment`: FK `FeeInstallment`, `on_delete=PROTECT`,
  `related_name="due_date_revisions"`; required.
- `previous_due_date`: nullable date; the effective date before the change.
- `revised_due_date`: required date; must differ from `previous_due_date`.
- `reason`: required `TextField`, non-blank, minimum 10 characters.
- `revised_by`: FK `accounts.User`, `on_delete=PROTECT`; the user who applied it.
- `source_request`: FK `DueDateChangeRequest`, `on_delete=PROTECT`, nullable,
  `related_name="resulting_revisions"`; set when the change came from an approved
  request.
- `created_at`: `auto_now_add`, indexed; ordering is `("-created_at",)`.
- Constraint `due_date_revision_changes_date`: `previous_due_date IS NULL` or
  `revised_due_date <> previous_due_date`.
- Rows are append-only: no update or delete path exists in services, views, or admin. A
  further change appends another row.
- A revision never writes to `FeePlan` amounts or `FeeInstallment.original_due_date`.

### DueDateChangeRequest (workflow, `apps.fees`)
- `installment`: FK `FeeInstallment`, `on_delete=PROTECT`,
  `related_name="due_date_change_requests"`; required.
- `requested_by`: FK `accounts.User`, `on_delete=PROTECT`; required.
- `requested_at`: `auto_now_add` datetime.
- `previous_due_date`: nullable date; snapshot of the effective date at request time.
- `proposed_due_date`: required date; must differ from `previous_due_date`.
- `reason`: required `TextField`, non-blank, minimum 10 characters.
- `status`: `PENDING` (default), `APPROVED`, `REJECTED`, `CANCELLED`; indexed.
- `decided_by`: FK `accounts.User`, `on_delete=PROTECT`, nullable; required once the
  request is decided.
- `decided_at`: nullable datetime; required once the request is decided.
- `decision_note`: optional `TextField`.
- Constraints:
  - `due_date_request_changes_date`: `previous_due_date IS NULL` or
    `proposed_due_date <> previous_due_date`.
  - `due_date_request_decision_metadata`: `PENDING` implies `decided_by` is null;
    every other status implies `decided_by` is set.
  - `due_date_request_one_pending_per_installment`: partial unique constraint on
    `installment` limited to `status = "PENDING"`.
- Role rule: `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` may apply revisions directly or
  decide requests; `ADMISSIONS` may only create `PENDING` requests. Approval creates an
  `InstallmentDueDateRevision` linked through `source_request` and transitions the
  request in the same transaction. Rejection creates no revision and moves no date.

### Plan versioning and immutability
- Approved plans are immutable: no edit-in-place, no delete, and no amount or date
  rewrite.
- Drafts remain editable until approval.
- A correction creates version `n + 1` as a new `DRAFT` with `supersedes` pointing at
  the previous approved plan.
- Approving the new version atomically marks the previous `APPROVED` plan
  `SUPERSEDED`, so exactly one `APPROVED` plan exists per admission.
- Old versions and their installments, due dates, and audit history are retained.
- Existing student assignments keep their original structure unless explicitly
  reassigned through an authorised action; reassignment is never a side effect.

### Six separate concepts

| # | Concept | Storage | Mutable? |
|---|---|---|---|
| 1 | Original approved installment amount | `FeeInstallment.amount` | No, after plan approval |
| 2 | Original suggested due date | `FeeInstallment.original_due_date` | Never |
| 3 | Revised student-specific due date | Latest applied `InstallmentDueDateRevision.revised_due_date` | Append-only |
| 4 | Actual amount paid | `Payment` + `InstallmentAllocation` (`apps.payments`) | Reversal only |
| 5 | Outstanding balance | Derived by selector (`amount - active allocations`) | Never stored |
| 6 | Actual payment date | `Payment.paid_on` (`apps.payments`) | Reversal only |

Keep these distinct in the schema, selectors, and UI; none may be produced by
overwriting another. A deadline change moves concept 3 only. It never alters an
installment amount, the plan totals, the original due date, recorded payments, or the
outstanding balance formula.

## Phase 5 — Payments, receipts, credit, refunds & reversals (implemented, `apps.payments`)

### ReceiptSequence
Per-centre, per-financial-year counter. Unique `(centre, financial_year)`,
`last_sequence >= 0`. Locked with `select_for_update()` during collection so two
concurrent payments cannot receive the same number.

### Payment
One collection against an admission. `centre` is the collection centre and may
differ from `admission.centre`. `amount > 0`; unique nullable `request_key` for
idempotency; status `POSTED`/`REVERSED`; `paid_on` drives the receipt's financial
year; indexes on `(centre, paid_on)` and `(admission, paid_on)`.

### Receipt
One-to-one with `Payment`. Stores `number` (unique), `centre`, `financial_year`,
`sequence`, `status`, `issued_by`, `issued_at`. Unique
`(centre, financial_year, sequence)`. Never edited, renumbered or reused; a
reversal flips `status` to `REVERSED` and keeps the number forever.

### InstallmentAllocation
Explicit amount on one `FeeInstallment`, sourced from a `Payment` or an
`AdvanceCredit` lot. `amount > 0`; source/link consistency check; ACTIVE rows have
no reversal metadata, REVERSED rows require actor/time/reason; unique
`(request_key, credit)` for credit applications. Rows are never deleted.

### AdvanceCredit
A credit lot from an overpayment (linked to its payment) or a fee-plan
reassignment (no single source payment). `original_amount > 0`,
`0 <= remaining_amount <= original_amount`, source/payment consistency, and at
most one overpayment lot per payment. `remaining_amount` is reconciled against
active applications and refunds by `credit_remaining_from_history()`.

### Refund and RefundCreditAllocation
Refund request against one credit lot: amount, reason, mode, requestor,
`original_payment`, status, approver, decision time, actual `refunded_on`, unique
`request_key`. Pending rows carry no decision metadata; decided rows require it;
approved rows require a refund date. `RefundCreditAllocation` is append-only and
unique per `(refund, credit)`.

### PaymentReversal
One-to-one with `Payment`, so a payment can never be reversed twice. Stores amount,
mandatory reason, requestor, approver, decision metadata and a unique
`request_key`. Approval compensates allocations, credit and receipt status without
deleting anything.

### FinancialAuditEvent
Append-only log of every financial event with admission, actor, optional payment/
installment references, amount and detail. `save()` on an existing row and
`delete()` both raise.

### FeePlanReassignment (`apps.fees`)
Workflow row for A4: `from_plan`, `to_plan`, mandatory `reason`, snapshotted
`previous_total`/`proposed_total`/`difference`, `had_payments`, requestor,
decision metadata and unique `request_key`. Checks enforce distinct plans, a
difference equal to the total delta, decision metadata, and one pending request per
admission. Approval reconciles money through allocation reversal + credit rather
than editing payments or receipts.

## Planned / logical entities (Phase 6)

- **FeeAdjustment**: deferred. Any adjustment to agreed fees requires a separately
  designed, authorised, and auditable workflow with its own approval rules. No discount,
  waiver, scholarship, or concession module exists.
- **StudentDocument**: student/admission, document type, private storage key, metadata. Deferred to a later phase.
- **Report/export models**: Phase 6 reporting and export audit, if persisted snapshots
  become necessary. Balances remain derived.

## Key rules
- A student can have more than one admission; model this explicitly.
- Preserve the fee agreement attached to an admission even if course defaults later change.
- Use DecimalField for money.
- Avoid a manually editable outstanding balance.
- Use deliberate on-delete policies and database constraints.
- Payment allocation policy is confirmed: staff-selected with a FIFO suggestion, partial and multi-installment payments, never beyond an installment's outstanding amount, excess held as advance credit.
- Receipt numbering is confirmed as centre-scoped per Indian financial year, generated by a locked counter and protected by unique constraints; numbers are never reused.
- Every payment, receipt, allocation, credit movement, refund and reversal is written through `apps.payments.services` inside a transaction with row locking; the Django admin for these models is read-only.
- Financial history is corrected by reversal and reconciliation, never by deletion. Payments, receipts and allocations keep their original rows.
- Do not store derived seat counts on `Batch`; calculate availability when admissions are implemented.
- Fee plans and installment schedules are implemented in `apps.fees`; see the Phase 4
  section above. No discount or waiver module exists or is planned, so keep discount
  fields off the academic masters and off fee plans.
- The GST rate is snapshotted per fee plan and immutable after approval; there is no
  global rate setting and no automatic course default.
- Approved fee plans are versioned rather than edited; corrections create a new version
  and supersede the old one while retaining its audit history. Draft component
  replacement is refused once due-date revision or request history exists, so audit rows
  are never orphaned.
- Due dates are target dates only. They must never block, reject, or divert a payment, and
  overdue status is always derived.
- Due-date revisions are only allowed on `COURSE` installments belonging to an `APPROVED`
  plan. `APPLICATION`/`ADMISSION` charges are stage-based and carry no date.
- No automatic late fees, interest, or penalties are computed or posted anywhere.
- All fee writes go through `apps.fees.services`; the Django admin for fee models is
  read-only.
- Student IDs are issued only by `apps.students.services.generate_student_id_for_enrollment()`; never compute them in views, templates, or JavaScript.

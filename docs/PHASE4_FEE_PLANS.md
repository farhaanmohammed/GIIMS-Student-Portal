# Phase 4 — Fee Plans & Installment Schedules

**Status:** **Implemented and verified** (2026-11-02). Migration
`apps/fees/migrations/0001_initial.py` exists and all 145 fee tests pass.
**Confirmed:** 2026-11-02
**Supersedes:** earlier aspirational references to discount/waiver approvals in
`PROJECT_SPEC.md` §6.4 and the original Phase 4 milestone wording in
`PROGRESS.md`.

This document is the implementation contract for Phase 4. It incorporates the
five confirmed GIIMS business decisions (see §1). Where any other document
disagrees, this document wins. Where the implementation deviates from the
original design, §16 records the deviation explicitly.

---

## 1. Confirmed decisions

| # | Decision | Consequence in this design |
|---|---|---|
| D1 | GST rate is stored **per fee plan**; July 2026 Logistics & SCM uses **18%**. No global rate, no automatic course default. | `FeePlan.gst_rate` + `gst_amount` are snapshotted and immutable after approval. |
| D2 | `ADMIN`, `MANAGEMENT`, `ACCOUNTS` may **apply** due-date revisions. `ADMISSIONS` may **request** but not approve or apply. | Two entities: `DueDateChangeRequest` (workflow) and `InstallmentDueDateRevision` (applied audit). |
| D3 | Due dates may move **earlier or later**; every change is audited; amounts never change. | No direction restriction. Constraint only requires the new date to differ. |
| D4 | `APPROVED` plans are **immutable**. Drafts are editable. Corrections create a **new version** that preserves the old one and its history. Existing student assignments keep their original structure unless explicitly reassigned. | `FeePlan.version`, `status=SUPERSEDED`, `supersedes` self-FK, and a versioning service. |
| D5 | All six fee components live in **one plan**. Application (INR 1,000) and Admission (INR 10,000) are **stage-based, not calendar-based**; four remaining installments carry suggested dates. All six are **part of** the INR 1,97,650 total, not additional. | `FeeInstallment.category` + a constraint forcing `APPLICATION`/`ADMISSION` to have no due date. Installment sum must equal `total_payable`. |

---

## 2. Policy rules (unchanged from the earlier confirmation)

### 2.1 Due dates are targets, never gates

- Every course installment carries an **original suggested due date**, used for
  reporting and follow-up only.
- Payments are accepted before, on, or after the suggested date. No service,
  view, selector, or template may block a payment because an installment is
  overdue.
- Overdue is **derived**, never stored and never enforced.
- The original due date is preserved permanently.
- A student-specific revision never mutates the approved plan or any amount.

### 2.2 Application and admission charges are stage-based

- `APPLICATION` (INR 1,000) is triggered at the application stage.
- `ADMISSION` (INR 10,000) is triggered at the admission stage.
- Neither carries a calendar due date, and neither is ever classified as
  overdue. Their "due" moment is the stage, tracked through payment activity in
  Phase 5, not a date in Phase 4.

### 2.3 No discounts, waivers, or penalties

- No discount, waiver, scholarship, or concession module, field, calculation,
  approval tier, or hierarchy.
- No automatic late fees, interest, or penalties. No `late_fee` field exists.
- Approved course fee and GST snapshots are kept intact.
- Exceptional adjustments, if ever required, need a separately designed,
  authorised, auditable workflow. Out of scope for Phase 4.

---

## 3. The distinct concepts that must never merge

| # | Concept | Storage | Mutable? |
|---|---|---|---|
| 1 | Original approved installment amount | `FeeInstallment.amount` | No, after plan approval |
| 2 | Original suggested due date | `FeeInstallment.original_due_date` | Never |
| 3 | Revised student-specific due date | Latest applied `InstallmentDueDateRevision.revised_due_date` | Append-only |
| 4 | Actual amount paid | `Payment` + `PaymentAllocation` (Phase 5) | Reversal only |
| 5 | Outstanding balance | Derived by selector | Never stored |
| 6 | Actual payment date | `Payment.paid_on` (Phase 5) | Reversal only |

A due-date change moves concept 3 only. Concepts 1, 2, 4 and 6 are untouched;
concept 5 is recomputed, never written.

---

## 4. Application and module layout

New Django app **`apps.fees`** (label `fees`), registered after
`apps.admissions`, routes at `/fees/` with namespace `fees`.

```
apps/fees/
├── models.py        # FeePlan, FeeInstallment,
│                    # InstallmentDueDateRevision, DueDateChangeRequest
├── services.py      # plan lifecycle, versioning, due-date workflow
├── selectors.py     # effective date, balances, overdue
├── forms.py
├── views.py
├── urls.py
├── admin.py         # read-only: all fee writes go through services
├── migrations/
│   └── 0001_initial.py
└── tests/
    ├── factories.py
    ├── test_models.py
    ├── test_services.py
    ├── test_due_date_workflow.py
    ├── test_selectors.py
    ├── test_views.py
    └── test_permissions.py
```

The app is registered in `INSTALLED_APPS` immediately after `apps.admissions`
and mounted at `/fees/` with URL namespace `fees`.

Payments, allocations, receipts, and reversals remain Phase 5.

---

## 5. Models

All money uses `DecimalField(12,2)`. Currency is INR for Version 1. All
services are atomic and re-derive totals server-side.

### 5.1 `FeePlan`

| Field | Type | Rules |
|---|---|---|
| `admission` | FK `admissions.Admission`, `PROTECT`, `related_name="fee_plans"` | Required |
| `version` | `PositiveIntegerField`, default `1` | Unique per admission |
| `status` | `TextChoices` `DRAFT` / `APPROVED` / `SUPERSEDED` / `CANCELLED`, default `DRAFT` | Indexed |
| `supersedes` | self-FK, `PROTECT`, `null=True`, `related_name="superseded_by"` | Lineage for corrections |
| `currency` | `CharField(3)`, default `"INR"` | Required |
| `course_fee` | `DecimalField(12,2)` | `>= 0`; taxable value, GST-exclusive |
| `gst_rate` | `DecimalField(5,2)` | `0 <= rate <= 100`; **snapshot per plan** |
| `gst_amount` | `DecimalField(12,2)` | Computed by service, never user input |
| `total_payable` | `DecimalField(12,2)` | Computed `course_fee + gst_amount` |
| `notes` | `TextField(blank=True)` | Optional |
| `approved_at` | `DateTimeField(null=True)` | Set by approval service |
| `approved_by` | FK `accounts.User`, `PROTECT`, `null=True` | Set by approval service |
| `created_at` / `updated_at` | `TimeStampedModel` | |

Status semantics:

- `DRAFT` — editable; not a student obligation yet.
- `APPROVED` — the student's binding fee structure. At most one per admission.
- `SUPERSEDED` — an older approved version retained for audit after a
  correction. Never deleted, never edited.
- `CANCELLED` — a draft abandoned before approval.

Constraints:

| Name | Rule |
|---|---|
| `fee_plan_course_fee_non_negative` | `course_fee >= 0` |
| `fee_plan_gst_amount_non_negative` | `gst_amount >= 0` |
| `fee_plan_total_payable_non_negative` | `total_payable >= 0` |
| `fee_plan_gst_rate_range` | `0 <= gst_rate <= 100` |
| `fee_plan_total_matches_components` | `total_payable = course_fee + gst_amount` |
| `fee_plan_approved_requires_metadata` | `status` in `DRAFT`/`CANCELLED`, or both `approved_at` and `approved_by` set |
| `fee_plan_unique_version_per_admission` | unique `(admission, version)` |
| `fee_plan_one_approved_per_admission` | unique `(admission)` where `status = APPROVED` |

Scoping: `FeePlan` has **no own `centre` column**, matching `Student`. Every
query and mutation resolves plans through
`admission__centre__in=user.get_accessible_centres()`, so a cross-centre plan
returns 404 instead of leaking existence.

**No discount, waiver, concession, or late-fee fields exist, by policy.**

### 5.2 `FeeInstallment`

| Field | Type | Rules |
|---|---|---|
| `fee_plan` | FK `FeePlan`, `PROTECT`, `related_name="installments"` | Required |
| `sequence` | `PositiveSmallIntegerField` | Unique per plan |
| `category` | `TextChoices` `APPLICATION` / `ADMISSION` / `COURSE` | Required; the stage for the first two |
| `description` | `CharField(200)` | Required |
| `amount` | `DecimalField(12,2)` | `> 0` |
| `original_due_date` | `DateField(null=True, blank=True)` | **Must be null** for `APPLICATION`/`ADMISSION`; **required** for `COURSE` |

Constraints:

| Name | Rule |
|---|---|
| `fee_installment_unique_sequence` | unique `(fee_plan, sequence)` |
| `fee_installment_amount_positive` | `amount > 0` |
| `fee_installment_due_date_matches_category` | `COURSE` ⇒ date set; `APPLICATION`/`ADMISSION` ⇒ date null |

Server-side service invariant (not a DB constraint): the sum of installment
amounts must equal `FeePlan.total_payable`.

Derived, never stored: `effective_due_date`, overdue membership, and payment
status.

### 5.3 `InstallmentDueDateRevision` (applied audit, append-only)

| Field | Type | Rules |
|---|---|---|
| `installment` | FK `FeeInstallment`, `PROTECT`, `related_name="due_date_revisions"` | Required |
| `previous_due_date` | `DateField(null=True)` | Effective date before the change |
| `revised_due_date` | `DateField()` | Required; must differ from previous |
| `reason` | `TextField()` | Required, non-blank, minimum 10 characters |
| `revised_by` | FK `accounts.User`, `PROTECT` | The user who **applied** it |
| `source_request` | FK `DueDateChangeRequest`, `PROTECT`, `null=True`, `related_name="resulting_revisions"` | Set when the change came from an approved request |
| `created_at` | `DateTimeField(auto_now_add=True, db_index=True)` | Timestamp |

- `due_date_revision_changes_date`: `previous IS NULL OR revised <> previous`.
- Ordering `("-created_at",)`.
- **Append-only**: no update or delete path in services, views, or admin.
  Effective due date is the latest revision, else the original.
- Creating a revision never writes to `FeePlan` or `FeeInstallment`.

### 5.4 `DueDateChangeRequest` (workflow — D2)

| Field | Type | Rules |
|---|---|---|
| `installment` | FK `FeeInstallment`, `PROTECT`, `related_name="due_date_change_requests"` | Required |
| `requested_by` | FK `accounts.User`, `PROTECT` | Required |
| `requested_at` | `DateTimeField(auto_now_add=True)` | Timestamp |
| `previous_due_date` | `DateField(null=True)` | Snapshot of the effective date at request time |
| `proposed_due_date` | `DateField()` | Required; must differ from previous |
| `reason` | `TextField()` | Required, non-blank, minimum 10 characters |
| `status` | `TextChoices` `PENDING` / `APPROVED` / `REJECTED` / `CANCELLED`, default `PENDING` | Indexed |
| `decided_by` | FK `accounts.User`, `PROTECT`, `null=True` | Required once decided |
| `decided_at` | `DateTimeField(null=True)` | Required once decided |
| `decision_note` | `TextField(blank=True)` | Optional |

Constraints:

| Name | Rule |
|---|---|
| `due_date_request_changes_date` | `previous IS NULL OR proposed <> previous` |
| `due_date_request_decision_metadata` | `PENDING` ⇒ `decided_by` null; other statuses ⇒ `decided_by` set |
| `due_date_request_one_pending_per_installment` | unique `(installment)` where `status = PENDING` |

`ADMISSIONS` creates `PENDING` requests only. `ADMIN`, `MANAGEMENT`, or
`ACCOUNTS` approve or reject them. Approval creates an
`InstallmentDueDateRevision` referencing the request and transitions the
request to `APPROVED` in the same transaction. Rejection creates no revision
and changes no dates.

### 5.5 Versioning model (D4)

```
version 1  APPROVED  ──►  SUPERSEDED        (retained, immutable, audited)
                           ▲
version 2  APPROVED  ──►  (current obligation)
```

- Approved versions are never edited or deleted.
- A correction calls `create_fee_plan_version()`, which copies the approved plan
  into a **new `DRAFT`** with `version = n + 1` and `supersedes = old`.
- Approving the new draft atomically sets the previous `APPROVED` plan to
  `SUPERSEDED`, so exactly one `APPROVED` plan remains.
- Existing assignments keep their original structure: the old version's amounts,
  installments, due dates, and revision history are untouched. Reassignment is
  an explicit authorised action, never a side effect.

---

## 6. Services (`apps/fees/services.py`)

All services are `transaction.atomic()`, re-validate inputs, and never trust
client-submitted totals.

| Service | Rules |
|---|---|
| `create_draft_fee_plan(admission, *, course_fee, gst_rate, components, actor, notes="")` | Requires `ENROLLED` admission and accessible centre. Computes `gst_amount = round(course_fee * gst_rate / 100, 2)` with `ROUND_HALF_UP`, then `total_payable`. Validates components (see below) and creates plan + installments. Rejects a second draft or any draft when an approved plan already exists. |
| `update_draft_fee_plan(plan, *, course_fee=None, gst_rate=None, components=None, notes=None, actor)` | `DRAFT` only, `select_for_update()`. Re-derives GST and the total; when `components` is omitted the stored installments are re-validated against the new total. Replacing components is refused if the plan already has any due-date revision or request history, so audit rows can never be orphaned. |
| `approve_fee_plan(plan, actor)` | `DRAFT` only, `select_for_update()`. Re-validates component sum. Sets `APPROVED` + metadata, and marks the previously approved plan `SUPERSEDED` when this is a new version. |
| `create_fee_plan_version(plan, actor)` | Source must be `APPROVED`. Creates new `DRAFT` with `version + 1`, `supersedes = plan`, copying amounts and components. Old plan untouched. Refuses when a draft version already exists. |
| `cancel_draft_fee_plan(plan, actor, reason)` | `DRAFT` only; sets `CANCELLED` and appends `Cancelled: <reason>` to `notes` so the cancellation is traceable. Cancelling an approved plan is a financial event and is out of scope. |
| `request_due_date_change(installment, *, proposed_due_date, reason, actor)` | Any authenticated role with centre access may request, including `ADMISSIONS`. Only `COURSE` installments on an `APPROVED` plan. Creates `PENDING` request. Rejects when a `PENDING` request already exists for that installment, and when the proposed date equals the current effective date. |
| `decide_due_date_change(request, *, approve, actor, note="")` | `ADMIN`/`MANAGEMENT`/`ACCOUNTS` only, centre-scoped and `select_for_update()`. Re-validates that the installment is still a `COURSE` row on an `APPROVED` plan and that the proposed date still differs from the effective date. Approve → creates the revision and marks `APPROVED`; reject → marks `REJECTED`. Both set `decided_by`/`decided_at`. |
| `apply_due_date_revision(installment, *, revised_due_date, reason, actor)` | Direct apply for `ADMIN`/`MANAGEMENT`/`ACCOUNTS`, only for `COURSE` installments on an `APPROVED` plan. Creates a revision with `source_request = None`. Never touches amounts or `original_due_date`. |

`DueDateChangeRequest.status` keeps a `CANCELLED` choice for completeness of the
workflow vocabulary, but no Phase 4 service produces it: requests are only ever
`APPROVED` or `REJECTED` by a decision. The database constraint treats any
non-`PENDING` status as decided, so introducing cancellation later does not
require a schema change.

### 6.1 Stage gate for due-date operations

Both due-date paths (request and direct apply) enforce the same guard:
the installment must belong to an `APPROVED` fee plan and must be a `COURSE`
component. Draft, superseded, and cancelled plans cannot have dates revised, and
`APPLICATION`/`ADMISSION` components raise a validation error because they are
stage-based and carry no date.

### 6.2 Component validation (D5)

`create_draft_fee_plan` validates:

1. There are exactly six components: one `APPLICATION`, one `ADMISSION`, and
   four `COURSE`.
2. `APPLICATION` and `ADMISSION` have no `original_due_date`.
3. Every `COURSE` component has a suggested due date.
4. Every amount is positive.
5. Sequences are unique and contiguous from 1.
6. **`sum(components.amount) == total_payable`** — so application and admission
   charges are inside the approved total, never added on top.

### 6.3 Explicit non-behaviours

No service computes a late fee, penalty, discount, waiver, or concession. No
service rejects a payment for being overdue.

---

## 7. Selectors (`apps/fees/selectors.py`)

Read-only, all accepting an explicit `as_of` date.

| Selector | Returns |
|---|---|
| `current_fee_plan(admission)` | The single `APPROVED` plan, else `None` |
| `plan_history(admission)` | All versions ordered by `version` |
| `effective_due_date(installment)` | Latest applied revision date, else `original_due_date`; always `None` for `APPLICATION`/`ADMISSION` |
| `installment_outstanding(installment, as_of=None)` | `amount − posted allocations` (equals `amount` until Phase 5). `as_of` is accepted now so the signature is stable when allocations arrive |
| `admission_fee_summary(admission, as_of=None)` | `total_billed`, `total_paid`, `total_outstanding`, `overdue_outstanding`, `not_yet_due_outstanding`, `undated_outstanding`; defaults to `timezone.localdate()` |
| `pending_due_date_requests(user, centre=None)` | Queue for approvers, scoped to `user.get_accessible_centres()` and optionally narrowed to one centre |

Rules:

- Undated `APPLICATION`/`ADMISSION` components are **never** overdue and are
  reported in `undated_outstanding`.
- `overdue_outstanding` uses the **effective** date, so an approved extension
  removes an installment from overdue; a pending request does **not**.
- Reports may show the original date alongside the effective date for audit.
- One outstanding definition, reused by screens and exports.

---

## 8. Workflows

### 8.1 Plan lifecycle

```
create draft ──► edit draft ──► approve ──► immutable obligation
                                   │
                                   └─ correction ──► new draft version
                                                        (approve supersedes old)
```

### 8.2 Due-date change by `ADMISSIONS` (request only)

```
ADMISSIONS creates request (PENDING)
        │
        ├─ ADMIN/MANAGEMENT/ACCOUNTS approve ──► revision created
        │                                        effective date moves
        │                                        request = APPROVED
        └─ reject ──► request = REJECTED, no date change
```

### 8.3 Due-date change by privileged staff (direct)

```
ADMIN/MANAGEMENT/ACCOUNTS ──► revision created immediately
                               (source_request left null)
```

Both paths produce the same immutable revision row with previous date, revised
date, reason, applying user, and timestamp.

---

## 9. Permissions matrix (confirmed D2)

| Action | ADMIN | MANAGEMENT | ACCOUNTS | ADMISSIONS |
|---|---|---|---|---|
| View plans, schedules, history | yes | yes | yes | yes |
| Create / edit draft plan | yes | yes | no | no |
| Approve plan | yes | yes | no | no |
| Create new plan version | yes | yes | no | no |
| Apply due-date revision directly | yes | yes | yes | **no** |
| Request a due-date change | yes | yes | yes | **yes** |
| Approve / reject a request | yes | yes | yes | **no** |

All actions remain centre-scoped through the admission. Superusers retain
all-centre access.

---

## 10. UI outline

- `/fees/plans/` — centre-scoped plan list filtered by student, status, and
  course, with HTMX table partials and preserved filters.
- `/fees/plans/<pk>/` — plan detail showing the GST snapshot
  (`course_fee`, `gst_rate`, `gst_amount`, `total_payable`), the six components
  with **original** and **effective** due dates side by side, outstanding and
  overdue as of today, and the version history.
- Draft editor — six ordered component rows; stage-based rows greyed out of the
  date field; live *preview only* totals; the server remains authoritative.
- Approve action with confirmation; approved plans render read-only with a
  "Create new version" action instead of edit.
- Installment action zone:
  - privileged roles see "Apply due-date revision";
  - `ADMISSIONS` sees "Request due-date change";
  - both capture a required reason (minimum 10 characters).
- Approver queue at `/fees/due-date-requests/` with approve/reject and decision
  note.
- Installment audit history listing every revision and request outcome.
- `STATUS` badges must not rely on colour alone.

---

## 11. Worked example — July 2026 Logistics & SCM (D1, D5)

Confirmed inputs:

- Approved course fee total: **INR 1,97,650**
- GST rate: **18%**, stored on the plan
- Application component: **INR 1,000**, stage-based, no date
- Admission component: **INR 10,000**, stage-based, no date
- Four `COURSE` components with suggested dates: **INR 1,86,650** combined

**A1 (resolved):** the INR 1,97,650 total is GST-**inclusive**, giving a taxable
value of INR 1,67,500 and GST of INR 30,150. The arithmetic is exact
(`167,500 × 1.18 = 197,650`). The service derives the GST from the taxable
value; a caller never submits the GST amount or the total.

Resulting plan:

| Field | Value |
|---|---|
| `course_fee` (taxable value) | 1,67,500.00 |
| `gst_rate` | 18.00 |
| `gst_amount` | 30,150.00 |
| `total_payable` | 1,97,650.00 |

Components (sum = 1,97,650 = `total_payable`, proving charges are not additive):

| Seq | Category | Amount | Due date |
|---|---|---|---|
| 1 | `APPLICATION` | 1,000.00 | none (stage-based) |
| 2 | `ADMISSION` | 10,000.00 | none (stage-based) |
| 3 | `COURSE` | entered per plan | suggested date entered per plan |
| 4 | `COURSE` | entered per plan | suggested date entered per plan |
| 5 | `COURSE` | entered per plan | suggested date entered per plan |
| 6 | `COURSE` | entered per plan | suggested date entered per plan |

**A2 (resolved):** the four `COURSE` components must sum to INR 1,86,650. The
individual split and the four suggested dates are **not** hardcoded in Phase 4:
they are entered on the draft plan for each admission and validated server-side
against the approved total. This keeps the confirmed structural rule (four dated
course installments totalling 1,86,650) without inventing a schedule that has not
been supplied as official data.

Test fixtures use a synthetic split of 40,000 / 50,000 / 50,000 / 46,650 with
dates in 2026–2027. Those numbers are test data only and are not an official
GIIMS schedule.

---

## 12. Phase 5 contract

1. `record_payment()` must never reject a payment because an installment is
   overdue.
2. `Payment.paid_on` (actual payment date) stays distinct from posting
   timestamp.
3. Allocations reduce outstanding; overdue then recalculates from the effective
   due date and remaining balance.
4. Allocating to `APPLICATION`/`ADMISSION` components is permitted and is how
   stage-based charges are settled.
5. Allocation policy (oldest-due-first versus staff-selected) remains open.

---

## 13. Test matrix

### 13.1 Models and constraints

- Duplicate `(admission, version)` rejected.
- Second `APPROVED` plan for one admission rejected.
- `SUPERSEDED` and `CANCELLED` plans do not count as active.
- Negative amounts rejected; `gst_rate` outside 0–100 rejected.
- `total_payable != course_fee + gst_amount` rejected by
  `fee_plan_total_matches_components`.
- `APPROVED` without `approved_at`/`approved_by` rejected.
- `COURSE` installment without a due date rejected.
- `APPLICATION`/`ADMISSION` installment **with** a due date rejected.
- Duplicate `(fee_plan, sequence)` rejected.
- `amount <= 0` rejected.
- Due-date revision with `revised == previous` rejected.
- Two `PENDING` requests for one installment rejected.
- Decided request without `decided_by` rejected.

### 13.2 GST snapshot (D1)

- A plan stores its own `gst_rate`; two plans with different rates are
  independent.
- 18% on a taxable value of 1,67,500 yields `gst_amount = 30,150.00` and
  `total_payable = 1,97,650.00`, with two-decimal rounding.
- Editing a later plan's rate never alters an earlier approved plan's
  `gst_rate`, `gst_amount`, or `total_payable`.
- There is no global rate setting and no course-level default to fall back on.

### 13.3 Components (D5)

- A plan with one `APPLICATION`, one `ADMISSION`, and four `COURSE` components
  validates.
- Missing or duplicated stage components are rejected.
- Component sum differing from `total_payable` is rejected.
- Application and admission amounts are inside the total, not added on top: a
  plan whose components sum to `total_payable` validates; summing the two
  stage charges separately is not required or permitted.

### 13.4 Immutability and versioning (D4)

- Editing an `APPROVED` plan raises and leaves all fields unchanged.
- Editing a `DRAFT` plan succeeds.
- `create_fee_plan_version` produces `version + 1`, `DRAFT`, and
  `supersedes = old`, copying amounts and components.
- Approving the new version sets the old to `SUPERSEDED` and leaves its
  installments and revision history intact.
- Exactly one `APPROVED` plan exists after the transition.
- The old version's amounts and due dates are unchanged by the new version.

### 13.5 Due-date workflow (D2, D3)

- `ADMISSIONS` request creates a `PENDING` record and does **not** change the
  effective date or overdue status.
- `ADMISSIONS` direct apply raises `PermissionDenied`.
- `ADMISSIONS` approving a request raises `PermissionDenied`.
- `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` can each apply directly and can each
  decide a request.
- Approving creates exactly one revision with previous date, revised date,
  reason, applying user, timestamp, and `source_request` link; the effective
  date moves.
- Rejecting creates no revision and leaves the effective date unchanged.
- Moving a date **earlier** and **later** both succeed.
- Revision reason shorter than 10 characters rejected.
- A second `PENDING` request for the same installment is rejected; a new request
  is allowed after the first is decided.
- Revising one student's installment does not alter any sibling installment,
  other students, or the plan.

### 13.6 Amounts are unaffected by deadlines

- After any revision, `FeeInstallment.amount`, `original_due_date`,
  `FeePlan.course_fee`, `gst_rate`, `gst_amount`, and `total_payable` are
  byte-for-byte unchanged.
- `original_due_date` is never rewritten.

### 13.7 Selectors and overdue

- Overdue classification correct for dates before, on, and after the effective
  date.
- An approved extension moves an installment out of overdue; a pending request
  does not.
- `APPLICATION`/`ADMISSION` components always report as `undated_outstanding`,
  never overdue.
- Earlier `as_of` dates reproduce historical positions.
- `admission_fee_summary` uses only the `APPROVED` plan; `SUPERSEDED` versions
  are excluded from balances.
- Outstanding equals billed minus allocations, computed once and reused.

### 13.8 Policy absence

- No model or service exposes a discount, waiver, concession, penalty, or late
  fee.
- `total_payable` equals `course_fee + gst_amount` with no reduction applied.

### 13.9 Permissions and scoping

- Anonymous users are redirected to login on every fee route.
- Cross-centre plan, installment, revision, and request access returns 404.
- State-changing endpoints reject `GET` with 405.
- Each role's allow/deny matches §9.

### 13.10 Phase 5 tests (specified now, implemented later)

- An overdue installment can still receive a payment.
- A payment after the due date is recorded with the correct actual date.
- Overdue outstanding decreases by the allocated amount.
- Allocation never mutates amounts or due dates.

---

## 14. Resolved vs remaining decisions

### Resolved before implementation

| Previously open | Resolution |
|---|---|
| GST rate configuration | Per plan, 18% for July 2026 Logistics & SCM; snapshotted |
| Who may revise due dates | `ADMIN`/`MANAGEMENT`/`ACCOUNTS` apply; `ADMISSIONS` request only |
| Earlier vs later revisions | Both permitted |
| Approved-plan corrections | New version; old retained and audited |
| Application/admission charge treatment | Stage-based components inside the plan, inside the total |
| **A1** — GST basis | Confirmed GST-inclusive: `course_fee 1,67,500 + GST 30,150 = 1,97,650` |
| **A2** — Course installment split | Confirmed the four `COURSE` components sum to 1,86,650; the per-admission split and dates are entered on the draft plan and validated server-side |

### Still open (does not block Phase 4)

- **A3 — Who creates draft plans:** §9 implements `ADMIN`/`MANAGEMENT` only.
  Confirm whether `ACCOUNTS` should also create or edit drafts.
- **A4 — Reassignment:** confirm the explicit authorised process for moving an
  existing admission to a corrected fee structure (who may trigger it, and
  whether it requires a reason field).
- **A5 — Allocation policy** (Phase 5): oldest-due-first versus staff-selected.

---

## 15. Explicit non-goals

- Discount, waiver, scholarship, or concession modules, fields, or approvals.
- Automatic late fees, interest, or penalties.
- Calendar due dates for application or admission charges.
- Editing or deleting an approved plan in place.
- Deleting plans, revisions, or requests.
- Payments, allocations, receipts, and reversals (Phase 5).

---

## 16. Implementation record

**Migration:** `apps/fees/migrations/0001_initial.py` (generated 2026-11-02)
creates the four models, all listed constraints, and their indexes.

**Verification (2026-11-02, PostgreSQL):**

| Check | Result |
|---|---|
| `uv run python manage.py check` | No issues |
| `uv run python manage.py makemigrations --check --dry-run` | No changes detected |
| `uv run python manage.py test apps.fees` | 145 tests, OK |
| `uv run python manage.py test` | 302 tests, OK |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | All files formatted |
| `npm run build` | HTMX/Alpine copied, Tailwind CSS minified |

### Recorded deviations from the original design text

1. **`fee_plan_approved_requires_metadata` also exempts `CANCELLED`.** A draft
   cancelled before approval has no approver, so the constraint is
   `status IN ('DRAFT','CANCELLED') OR (approved_at AND approved_by)`. The
   earlier text said only `status = 'DRAFT'`, which would have made cancellation
   impossible.
2. **`cancel_draft_fee_plan` records the reason.** The reason is appended to
   `FeePlan.notes` as `Cancelled: <reason>` because `FeePlan` has no dedicated
   cancellation-reason column. This keeps the audit trail without adding a field
   the design did not specify.
3. **`update_draft_fee_plan` refuses component replacement after due-date
   history exists.** Replacing installments would cascade-delete or orphan
   revision/request rows, so the service raises instead. Amount-only edits remain
   possible while the existing component sum still matches the recomputed total.
4. **Due-date operations are restricted to `COURSE` installments on `APPROVED`
   plans.** This was implied by the stage-based rule and D4, but is stated here
   as an enforced guard rather than an assumption.
5. **`pending_due_date_requests` takes `user` as its first argument.** The queue
   must be scoped to the requesting approver, so the signature is
   `pending_due_date_requests(user, centre=None)`. The original table omitted the
   argument.
6. **`installment_outstanding` and `admission_fee_summary` accept `as_of=None`**
   and default to `timezone.localdate()`, while still honouring an explicit
   historical date.
7. **Fees admin is read-only.** `FeePlan`, `FeeInstallment`,
   `InstallmentDueDateRevision`, and `DueDateChangeRequest` cannot be added,
   changed, or deleted in Django admin. Financial writes must pass through the
   services so totals are re-derived and audit rows are created.
8. **No `role_required` double-wrapping.** Views use a small
   `RoleRequiredViewMixin` placed after `LoginRequiredMixin`, so anonymous users
   are redirected to login and authenticated but unauthorised users receive 403.

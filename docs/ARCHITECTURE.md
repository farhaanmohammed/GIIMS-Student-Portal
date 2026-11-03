# GIIMS Architecture

## Default architecture
A modular Django monolith using PostgreSQL, Django Templates, HTMX, Alpine.js, and compiled Tailwind CSS.

## Request flow
Browser -> Django URL/view -> form validation and authorization -> service layer for business operations -> Django ORM/PostgreSQL -> template, HTMX partial, CSV, or PDF response.

## Domain boundaries
- `accounts`: users, groups, roles, access policy.
- `centres`: centre master and centre membership/access.
- `academics`: academic years, courses, batches.
- `students`: durable student identity and documents.
- `admissions`: admission/enrolment events.
- `fees`: owns `FeePlan`, `FeeInstallment`, `InstallmentDueDateRevision`, `DueDateChangeRequest`, and `FeePlanReassignment`, and hosts the balance/overdue selectors. There is no discount or waiver module, by policy.
- `payments`: owns `Payment`, `Receipt`, `ReceiptSequence`, `InstallmentAllocation`, `AdvanceCredit`, `Refund`, `RefundCreditAllocation`, `PaymentReversal`, and `FinancialAuditEvent`. It hosts collection, allocation, receipt numbering, printable/PDF receipt rendering, credit, refund, and reversal services. There is no separate `receipts` app.
- `reports`: authorized centre-scoped query, ledger, operational/financial report, audit-trail viewer, live dashboard summary, and CSV/PDF export workflows.
- `core`: shared base models, permission helpers, CSV formula-injection neutralization, and zero-dependency `%PDF-1.4` generation utilities.

Create apps as needed; do not generate empty apps solely to match a diagram. Keep views thin and put multi-model business operations in services. Use selectors/query helpers where they make complex read logic reusable.

`academics` owns the portal-wide `AcademicYear` and `Course` masters and the centre-scoped `Batch` entity, with list/create/update/toggle views under `/academics/`. Reads are open to every authenticated role, writes require `ADMIN` or `MANAGEMENT`, and batch access is filtered through `Batch.objects.for_user()`. Fee/pricing templates and seat-count enforcement deliberately live outside this app.

`fees` (Phase 4) scopes fee plans through `admission__centre__in=user.get_accessible_centres()` rather than an own `centre` column, matching how `Student` is scoped, so a plan outside the actor's centres resolves to 404 instead of leaking existence. GST rate is stored and snapshotted on each fee plan (18% for July 2026 Logistics & SCM), with no global rate and no course default, and it is immutable after approval. Approved plans are immutable in their amounts: corrections create a new plan version linked through `supersedes`, never an in-place edit, the previous version becomes `SUPERSEDED` and is retained with its history, and exactly one `APPROVED` plan exists per admission. Installment due dates are target dates only. They drive overdue reporting and never gate, block, or invalidate a payment, and no late fee or penalty is computed anywhere. Due-date changes follow two paths: `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` apply revisions directly, while `ADMISSIONS` can only create a `PENDING` `DueDateChangeRequest` that a privileged role must decide; applied changes are recorded as append-only `InstallmentDueDateRevision` rows. Fees are components of one plan: application and admission charges are stage-based and carry no calendar dates, the four course installments carry suggested dates, and all six components sum to the approved total rather than being added on top of it. See [`PHASE4_FEE_PLANS.md`](PHASE4_FEE_PLANS.md) for the contract and its implementation record.

`payments` (Phase 5 & Phase 6) owns money movement and official receipts. `Payment.centre` is the collection centre and is distinct from `admission.centre`; all queries are scoped through the admission's centre and every service re-checks the actor's role and centre access. Receipts use `GIIMS/{CENTRE_CODE}/{FYyy-yy}/{000001}` with one concurrency-safe `ReceiptSequence` row per `(centre, financial year)`, locked with `select_for_update()` inside the payment transaction; numbers are never reused, even after reversal. Printable HTML (`/payments/<pk>/receipt/`) and server-generated PDF (`/payments/<pk>/receipt/pdf/`) receipt views preserve the original receipt number, display a prominent `REVERSED / VOID` notice when reversed, and append a `RECEIPT_REPRINTED` `FinancialAuditEvent` without creating duplicate payment rows. Allocations are explicit rows separate from payments, so partial, multi-installment and advance-credit applications are traceable and reversible without deletion. Unallocated money is held as `AdvanceCredit` lots; refunds consume a lot and require `ADMIN`/`MANAGEMENT` approval; payment reversals compensate allocations, credit and receipt status without deleting or renumbering anything. Fee-plan reassignment (`FeePlanReassignment`) is requested by `ACCOUNTS` and approved only by `ADMIN`/`MANAGEMENT`; approval reverses old-plan allocations, returns credit-funded amounts to their lots, converts payment-funded amounts into a `REASSIGNMENT` credit lot, approves the new version, and reapplies credit FIFO, so no payment or receipt is lost. See [`PHASE5_PAYMENTS.md`](PHASE5_PAYMENTS.md).

`reports` (Phase 6) provides centre-scoped read-only selectors and views under `/reports/` for Student Fee Ledgers, the Student & Admission Register, Student-Wise & Aggregate Outstanding Reports, Overdue Installment Reports, Collection & Receipt Registers (explicitly separating collection-centre vs admission-centre totals), Adjustments (Refunds, Reversals & Reassignments), and the Financial Audit Trail, plus live KPIs on the main dashboard (`/`). `ADMISSIONS` can view the Reports Hub, Admission Register, and read-only Student Fee Ledgers for accessible centres; `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` have access to all financial reports, the audit trail, and CSV/PDF exports. All CSV exports neutralize spreadsheet formula injection (`apps.core.exporters.sanitize_csv_cell`).

## Multi-centre access
Use a shared database with explicit centre relationships. Enforce centre scoping server-side in all queries, reports, receipt downloads, and mutations. Cross-centre visibility must be explicitly granted.

## Financial consistency
Post payments and allocations atomically. Use database constraints and locking where concurrency matters. Posted payments are corrected through traceable reversals, not deletion. Outstanding values must have one documented calculation definition.

- Due dates are informational targets; payment posting must never be rejected because an installment is overdue, and no late fee or penalty is computed anywhere.
- Outstanding and overdue have exactly one derived definition, evaluated against an explicit as-of date; balances are never stored as editable values.
- Fee plans snapshot their GST rate and amounts. Approved amounts are never edited in place and are only superseded by a new plan version, so a later rate or course-default change cannot rewrite an existing obligation.

# GIIMS Student Portal — Project Progress Tracker

This document tracks implementation progress across phases, verification runs, test results, and architectural decisions for the GIIMS Student Portal.

---

## High-Level Milestone Tracker

| Phase | Milestone | Scope | Status | Completed Date |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | **Foundation, Auth & Centre Scoping** | PostgreSQL setup, custom `User` model, role-based permissions, centre-level scoping, login/logout, Tailwind CSS v4, HTMX, Alpine.js, test suite & Ruff linter. | **Completed** | 2026-10-24 |
| **Phase 2** | **Academic Masters & Structure** | `AcademicYear`, `Course`, `Batch` masters, centre-scoped batch management, admin registration, role-gated CRUD/HTMX screens. | **Completed** | 2026-10-25 |
| **Phase 3** | **Student & Admission Management** | Durable `Student` profile, `Admission` lifecycle (`ENQUIRED` to `ENROLLED`), concurrency-safe student ID generation on enrollment, centre-scoped views with search/filters. Document uploads deferred. | **Completed** | 2026-11-02 |
| **Phase 4** | **Fee Plans & Installment Schedules** | `FeePlan` with a per-plan GST snapshot and six components per plan (application/admission stage-based plus four dated course installments), approved-plan immutability with versioned corrections, due-date request/approval workflow with append-only applied revisions, and as-of overdue calculation. No discount or waiver module. | **Completed** | 2026-11-02 |
| **Phase 5** | **Payments, Allocations & Traceable Receipts** | Concurrency-safe payments, sequential receipt numbers, FIFO allocations, reversal/correction workflows. | Planned | — |
| **Phase 6** | **Ledgers, Reports, Audit & Deployment** | Student ledger, collection and outstanding reports, audit logging, security review, production checklist. | Planned | — |

---

## Phase Details & Changelog

### Phase 1: Foundation, Authentication & Centre Scoping
- **Status:** Completed
- **Date:** 2026-10-24

#### Implemented Features
1. **Environment & Database Configuration**:
   - PostgreSQL default database configuration in [`config/settings.py`](../config/settings.py) loaded via `python-dotenv`.
   - Timezone set to `Asia/Kolkata`.
   - `AUTH_USER_MODEL = "accounts.User"` configured prior to initial migrations.
2. **Custom User & Role System (`apps.accounts`)**:
   - Custom `User` model inheriting from `AbstractUser`.
   - Roles: `ADMIN`, `ADMISSIONS`, `ACCOUNTS`, `MANAGEMENT`.
   - Operational centre scoping with `primary_centre` (ForeignKey) and `centres` (ManyToManyField).
   - Permission helper properties: `has_all_centres_access`, `get_accessible_centres()`, `can_access_centre(centre)`.
3. **Centre Entity (`apps.centres`)**:
   - `Centre` model (`code`, `name`, `address`, `email`, `phone`, `is_active`).
   - Registered in Django Admin.
4. **Core Security & Scoping Utilities (`apps.core`)**:
   - `TimeStampedModel` providing `created_at` and `updated_at`.
   - `CentreScopedQuerySet` with `.for_user(user)` automatic row-level filtering.
   - `CentreScopedModel` abstract base model.
   - `CentreAccessRequiredMixin` for class-based views.
   - `@role_required` view decorator for role authorization.
5. **Authentication Flow & Frontend Foundation**:
   - Institutional login view (`GIIMSLoginView`) and CSRF logout view (`GIIMSLogoutView`).
   - Tailwind CSS v4 CLI compilation pipeline (`npm run build`, `npm run watch:css`).
   - HTMX and Alpine.js vendor assets stored locally in `static/dist/js/`.
   - Global layout with HTMX CSRF header handling in `templates/base.html`.
   - Protected dashboard view (`config/views.py`) with `@login_required`.
6. **Code Quality & Testing**:
   - Configured `ruff` linter and formatter in `pyproject.toml`.
   - 14 automated tests covering model constraints, centre scoping querysets, role decorators, and authentication flows.

#### Verification Run Results
- `uv run python manage.py check`: Passed (0 issues).
- `uv run python manage.py test`: Passed (14 tests in 2.3s).
- `uv run ruff check .`: Passed (All checks passed).
- `uv run ruff format --check .`: Passed (All files formatted).
- `npm run build`: Passed (Tailwind CSS v4 minified build).

### Phase 2: Academic Masters & Structure
- **Status:** Completed
- **Date:** 2026-10-25

#### Implemented Features
1. **Academics Application (`apps.academics`)**:
   - `AcademicsConfig` (`verbose_name = "Academics & Courses"`), registered in `INSTALLED_APPS` after `apps.centres`.
   - Routes mounted at `/academics/` in [`config/urls.py`](../config/urls.py) with URL namespace `academics`.
2. **Academic Year Master (`AcademicYear`, portal-wide)**:
   - `label` (unique free text, max 20 chars, stripped on `clean()`/`save()`; no year-format convention enforced).
   - `start_date` and `end_date`, with check constraint `academic_year_end_after_start`.
   - `is_active` (default `False`, indexed) and an `is_current` property that reports whether today falls within the date range.
3. **Course Master (`Course`, portal-wide)**:
   - `code` (unique, max 20 chars) validated against `^[A-Z0-9][A-Z0-9-]*$` and normalized to uppercase in `save()` and `clean_fields()`; `name`; `duration_months` (1–120, default 12); `description`; `is_active` (default `True`, indexed).
   - No fee, pricing, or discount fields: fee templates are deferred to Phase 4.
4. **Batch Master (`Batch`, centre-scoped)**:
   - Extends `CentreScopedModel`, so `centre` is required and `Batch.objects.for_user(user)` applies row-level scoping.
   - `course` and `academic_year` foreign keys use `on_delete=PROTECT`; `code` (max 30 chars); `start_date`/`end_date`; `capacity` (nullable, no default, meaning "unspecified"); `is_active` (default `True`, indexed).
   - Constraints: `batch_unique_code_per_centre_course_year`, `batch_end_after_start`, `batch_capacity_positive` (null or at least 1).
   - No derived seat counters or enrolment totals are stored; capacity enforcement is deferred.
5. **Django Admin**:
   - `AcademicYear`, `Course`, and `Batch` registered with list displays, list filters, and search fields; `BatchAdmin` additionally uses `list_select_related` for `centre`, `course`, and `academic_year`, plus `autocomplete_fields` for the same relations.
6. **Forms & Server-Side Validation**:
   - `AcademicYearForm` validates a non-blank label and requires `end_date` after `start_date`.
   - `CourseForm` redeclares `code` so lowercase input is stripped and upper-cased before the uppercase-only validator runs.
   - `BatchForm` is user-aware: centre choices come from `user.get_accessible_centres()`, the submitted centre is re-validated in `clean_centre()` (no IDOR), capacity must be at least 1 or blank, dates are checked, and duplicates are rejected with a field-level error.
7. **Views, URLs, Authorization & Scoping**:
   - 12 named routes (list/create/update/toggle for each entity) grouped in three CRUD areas.
   - Reads are available to every authenticated role; writes are restricted to `ADMIN`/`MANAGEMENT` via `role_required`, returning 403 for other authenticated roles.
   - Anonymous requests are redirected to login with a `next` parameter; toggles are POST-only and return 405 for GET.
   - `BatchUpdateView` and `BatchToggleView` resolve objects through `Batch.objects.for_user()`, so inaccessible records resolve to 404.
8. **Lists, Filtering & Pagination**:
   - 25 rows per page with elided page ranges; malformed or out-of-range page numbers fall back to the nearest valid page.
   - Filters are whitelisted: `q` (course code/name, max 100 chars), `is_active` (`1`/`0` only), `centre` and `academic_year` (validated against accessible centres and existing academic years).
   - HTMX requests receive only the table partial (`HX-Request` header); the page number and active filters are preserved in pagination links.
9. **Templates & Frontend**:
   - 6 page templates and 3 HTMX table partials under `templates/academics/`.
   - CSRF tokens on all create/update and toggle forms, error summaries, filter-aware empty states, text status badges, and `hx-confirm` prompts on status toggles.
   - Sidebar links for Courses, Batches, and Academic Years wired in `templates/base.html`.
10. **Migrations & Phase 1 Drift Cleanup**:
    - `apps/academics/migrations/0001_initial.py` creates the three models and their constraints.
    - State-only `AlterModelOptions` migrations (`apps/accounts/migrations/0002_alter_user_options.py`, `apps/centres/migrations/0002_alter_centre_options.py`) align `ordering` with the code (list to tuple); no DDL changes.
11. **Integration Fixes**:
    - `Course.clean_fields()` normalizes the code before validators run, so `full_clean()` accepts lowercase input.
    - `User.can_access_centre()` resolves pk/code identifiers against the database and refuses inactive centres for all-centres users, closing an authorization gap.

#### Verification Run Results
Executed from the repository root on 2026-10-25:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test` → `Found 140 test(s).`, `Ran 140 tests in 7.051s`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `63 files already formatted`
- `npm run build` → `tailwindcss v4.3.3`, `Done in 49ms`
- `uv run python manage.py check --deploy` → 6 warnings, all from local-development configuration: `mail.E001` (console email backend), `security.W004` (no HSTS), `security.W008` (no SSL redirect), `security.W012` (session cookie not secure), `security.W016` (CSRF cookie not secure), `security.W018` (`DEBUG=True`). These are deferred production hardening items for Phase 6, not Phase 2 regressions.

#### Test Breakdown
140 tests in total:
- 14 pre-existing Phase 1 tests: `apps/accounts/tests.py` (7), `apps/centres/tests.py` (3), `apps/core/tests.py` (4).
- 63 model/permission/admin tests across 8 classes: `test_models.py` (35), `test_permissions.py` (20), `test_admin.py` (8).
- 61 form/view/HTMX tests across 12 classes: `test_forms.py` (25), `test_views.py` (36).
- 2 regression tests added during integration in `apps/accounts/tests.py`.

#### Known Limitations
- All-centres users (`ADMIN`, `MANAGEMENT`, superusers) see batches belonging to inactive centres, because `Batch.objects.for_user()` returns the unnarrowed queryset for that group. This is deliberate privileged-administration behaviour.
- `BatchForm` centre choices come from `get_accessible_centres()`, which returns active centres only, so editing a batch whose centre was later deactivated requires a change to that whitelist.
- CSRF is enforced by middleware and rendered tokens are asserted in the template tests, but Django's test client bypasses CSRF validation, so an end-to-end CSRF-protected POST is not covered by automated tests.
- A cross-centre batch update returning 404 is only covered indirectly: `ADMIN`/`MANAGEMENT` have all-centres access by design, so the 404 branch is exercised by list-scoping and form centre-queryset tests rather than a direct cross-centre write test.

### Phase 3: Student & Admission Management
- **Status:** Completed
- **Date:** 2026-11-02

#### Confirmed Business Rules
- There is no separate admission number. `Student.student_id` is the single identifier.
- The student ID format is `{centre code}-{batch code}-{course code}_{NNN}` (for example `TVM-ASP-AD_001`).
- The ID is assigned only when an admission becomes `ENROLLED`; it is never generated at enquiry time.
- The sequence resets each academic year in practice because the batch code changes each year.
- An issued ID is immutable: a student changing course keeps the original `student_id` and gains a new `Admission`.
- Numeric suffix is zero-padded to three digits and grows naturally past 999.

#### Implemented Features
1. **Students Application (`apps.students`)**:
   - `Student` model with durable identity plus personal, contact, and guardian details; `Gender` `TextChoices`.
   - `student_id` is unique, indexed, nullable until enrollment, and never regenerated.
   - `StudentIdSequence` counter model: one row per ID prefix with a `last_number` field and a non-negative check constraint.
2. **Admissions Application (`apps.admissions`)**:
   - `Admission` extends `CentreScopedModel` and links `Student`, `Course`, `Batch`, and `AcademicYear` with `on_delete=PROTECT`.
   - Status lifecycle via `Admission.Status` (`ENQUIRED`, `ENROLLED`, `DROPPED`, `COMPLETED`).
   - `enquired_at`, `enrolled_at`, and `enrolled_by` record the lifecycle.
   - Partial unique constraint `admission_unique_student_batch` prevents duplicate active admissions for the same student and batch.
3. **Concurrency-Safe Student ID Generation (`apps.students.services`)**:
   - `build_student_id_prefix(admission)` composes the prefix from `centre.code`, `batch.code`, and `course.code`, captured at enrollment so later master edits never rewrite an issued ID.
   - `_next_sequence_number(prefix)` locks the `StudentIdSequence` row with `select_for_update()` inside a transaction and increments it, so two concurrent enrollments cannot receive the same number.
   - `generate_student_id_for_enrollment(admission)` is idempotent, locks the student row, and defensively skips any ID that already exists (supports pre-counter legacy rows).
   - No `MAX(number) + 1` or `COUNT(*) + 1` numbering: allocation is serialized on the locked counter row and uniqueness is still enforced by the database.
4. **Enrollment & Enquiry Flow**:
   - `enroll_admission(admission, user)` service performs the `ENQUIRED` → `ENROLLED` transition atomically, records the actor and timestamp, and assigns the student ID.
   - Combined enquiry creation: `StudentEnquiryForm` + `AdmissionEnquiryForm` create a `Student` and its first `Admission` in one atomic transaction.
   - Writes are limited to `ADMIN`, `MANAGEMENT`, and `ADMISSIONS` (superusers included); `ACCOUNTS` and other roles are rejected.
   - `AdmissionEnquiryForm` is user-aware: centre choices come from `get_accessible_centres()` and the submitted centre is re-validated in `clean_centre()` (no IDOR).
   - Enrollment re-resolves the admission within the actor's accessible centres, so inaccessible records return 404.
5. **Views, Lists & Filtering**:
   - `/students/` list and detail, `/admissions/` list and detail, plus enquiry creation and a POST-only enrollment endpoint.
   - Student list filters by free-text query and enrollment state; admission list filters by free-text query and status; both are validated against whitelists.
   - Student and admission queries are centre-scoped through admissions and use `select_related`/`prefetch_related` to avoid N+1 queries.
   - Sidebar links for Admissions and Students in `templates/base.html`.
6. **Admin & Tests**:
   - `StudentAdmin` and `AdmissionAdmin` with list displays, filters, search, and read-only audit fields.
   - `StudentIdSequenceAdmin` is read-only and has add/change/delete disabled: IDs are issued only by the service.
   - 17 tests in `apps/students/tests.py`: ID format and padding, sequence increments, idempotency, immutability on course change, uniqueness enforcement, legacy-row continuation, enrollment service behaviour, and role/anonymous/POST-only access rules.

#### Verification Run Results
Executed from the repository root on 2026-11-02:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test` → `Found 157 test(s).`, `Ran 157 tests in 12.633s`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `All files formatted`
- `uv run python manage.py migrate` → `students.0001_initial`, `admissions.0001_initial`, `students.0002_studentidsequence` applied.

#### Known Limitations / Deferred
- **Document uploads**: deferred to a later phase. No `StudentDocument` model, private storage, upload validation, or authorized download view exists yet.
- **Batch capacity**: still not enforced at enrollment; `Batch.capacity` remains informational.
- **Concurrency tests**: sequence allocation is protected by row locking and covered by sequential tests, but no PostgreSQL-backed parallel test exercises real lock contention. SQLite (used by the local test run) does not reproduce PostgreSQL locking.
- **CSRF on enrollment**: enforced by middleware and rendered in the template, but Django's test client bypasses CSRF validation, so the protected POST is not asserted end-to-end.

### Phase 4: Fee Plans & Installment Schedules
- **Status:** Completed
- **Date:** 2026-11-02

#### Confirmed Business Policies
- The GST rate is stored per fee plan; July 2026 Logistics & SCM uses 18%. The rate, GST amount, and total are snapshotted when the plan is approved and are immutable afterwards. There is no global GST rate setting and no automatic course default.
- `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` may apply due-date revisions; `ADMISSIONS` may only request them. Every change records the previous date, the revised date, a required reason, the applying user, and a timestamp.
- Due dates may be moved earlier or later. Every change is audited, and an installment amount never changes as a result.
- Approved plans are immutable, while drafts remain editable. A correction creates a new plan version that preserves the old version and its audit history. Existing admissions retain their original fee structure unless explicitly reassigned.
- Every plan carries six components: the application charge of INR 1,000 and the admission charge of INR 10,000 are stage-based with no calendar date, while the four course installments carry suggested dates. All six sit inside the approved total of INR 1,97,650 and are not additional to it.
- Due dates are target dates only. Payments are accepted before, on, or after the due date and are never blocked, rejected, or queued for approval for being overdue.
- Overdue is a derived calculation, not an enforcement gate: it combines the unpaid balance with the effective due date against a chosen as-of date.
- The original suggested due date is preserved permanently. A revision changes only the student-specific effective date and never the approved plan, its GST snapshot, or any installment amount.
- There are no automatic late fees, interest, or penalties, and no `late_fee` field exists.
- No discount, waiver, scholarship, or concession module, calculation, approval limit, or hierarchy exists. Exceptional fee adjustments require a separately designed, authorised, and auditable workflow, which is out of scope for Phase 4.

#### Design Reference
- [PHASE4_FEE_PLANS.md](PHASE4_FEE_PLANS.md) — canonical Phase 4 design. Where this document, `PROJECT_SPEC.md`, or any earlier plan disagrees with it, the design document wins.

#### Implemented Features
1. **Fees Application (`apps.fees`)**:
   - `FeesConfig` registered after `apps.admissions`; routes mounted at `/fees/` with namespace `fees`.
   - `FeePlan`, `FeeInstallment`, `InstallmentDueDateRevision`, and `DueDateChangeRequest` with all designed constraints and indexes.
   - `apps/fees/migrations/0001_initial.py` creates the schema.
2. **Plan Lifecycle (`apps/fees/services.py`)**:
   - `create_draft_fee_plan`, `update_draft_fee_plan`, `approve_fee_plan`, `create_fee_plan_version`, and `cancel_draft_fee_plan`, all atomic and re-validating totals server-side.
   - GST is derived from the plan's taxable value and rate with `ROUND_HALF_UP`; the client never submits GST, the total, or a plan version.
   - Exactly one approved plan per admission is enforced by a partial unique constraint; approving a correction supersedes the previous version and leaves its installments and audit history intact.
   - Component validation enforces exactly six rows (one `APPLICATION`, one `ADMISSION`, four `COURSE`), positive amounts, contiguous sequences, dates only on `COURSE` rows, and a component sum equal to `total_payable`.
3. **Due-Date Workflow (D2, D3)**:
   - `InstallmentDueDateRevision` is append-only at the model level (`save()` on an existing row and `delete()` both raise) and records previous date, revised date, reason, applying user, timestamp, and an optional source request.
   - `DueDateChangeRequest` supports `PENDING` → `APPROVED`/`REJECTED` with one pending request per installment and mandatory decision metadata.
   - `ADMISSIONS` can only request; `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` can apply directly or decide requests. Both paths are restricted to `COURSE` installments on `APPROVED` plans.
   - A revision moves the effective date only. Amounts, `original_due_date`, plan totals, and the GST snapshot are never rewritten.
4. **Selectors (`apps/fees/selectors.py`)**:
   - `current_fee_plan`, `plan_history`, `effective_due_date`, `installment_outstanding`, `admission_fee_summary`, and `pending_due_date_requests`.
   - Overdue is derived against an explicit `as_of` date from the unpaid balance and effective due date. Stage-based charges are always reported as undated and never overdue. A pending request does not remove an installment from overdue; an approved extension does.
5. **Views, URLs & Templates**:
   - Centre-scoped plan list with search, status filter, pagination, and an HTMX table partial; plan detail with the GST snapshot, original vs effective dates side by side, outstanding/overdue position, version history, and audit lists.
   - Draft editor for the six components; POST-only approve, new-version, cancel, and due-date decision endpoints; approver queue at `/fees/due-date-requests/`.
   - Fee plan section on the admission detail page, plus sidebar links under "Finance & Collections" and a role-gated due-date request link.
   - CSRF tokens on every state-changing form; `GET` on POST-only endpoints returns 405.
6. **Read-Only Admin**:
   - All four fee models are registered with add/change/delete disabled, so financial writes cannot bypass the services.
7. **Tests**:
   - 145 tests across `test_models.py`, `test_services.py`, `test_due_date_workflow.py`, `test_selectors.py`, `test_views.py`, and `test_permissions.py`, with shared synthetic factories.
   - Covers database constraints, GST snapshot arithmetic, component totals, versioning and immutability, the due-date workflow and role matrix, amount invariants after revisions, overdue boundaries, policy absence (no discount/waiver/penalty fields), centre scoping, and 404/403/405 behaviour.

#### Verification Run Results
Executed from the repository root on 2026-11-02 against PostgreSQL:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test apps.fees` → `Ran 145 tests`, `OK`
- `uv run python manage.py test` → `Ran 302 tests`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `105 files already formatted`
- `npm run build` → HTMX/Alpine assets copied; Tailwind CSS v4 minified in 52ms

#### Recorded Deviations
See [`PHASE4_FEE_PLANS.md`](PHASE4_FEE_PLANS.md) §16. In brief:
- `fee_plan_approved_requires_metadata` exempts `CANCELLED` as well as `DRAFT`.
- `cancel_draft_fee_plan` appends `Cancelled: <reason>` to `notes`.
- `update_draft_fee_plan` refuses to replace components once due-date history exists.
- Due-date operations are restricted to `COURSE` installments on `APPROVED` plans.
- `pending_due_date_requests(user, centre=None)` takes the approver as its first argument.
- Fees admin is read-only and views use a `RoleRequiredViewMixin` after `LoginRequiredMixin`.

#### Known Limitations / Deferred
- Payments, allocations, receipts, and reversals remain Phase 5. `installment_outstanding` currently equals the approved amount because no allocations exist yet.
- Outstanding is not yet reduced by payments, and no ledger or collection report consumes the summary selectors.
- The four `COURSE` amounts and suggested dates are entered per plan; no official July 2026 schedule is hardcoded. Test dates and amounts are synthetic.
- Batch capacity, document uploads, and refund/reversal policy remain open or deferred.
- CSRF is enforced by middleware but Django's test client bypasses validation, so protected POSTs are not asserted end-to-end.

---

## Open Decisions & Items to Confirm Before Next Phases

1. **Admissions (Phase 3 follow-ups)**:
   - Whether enrollment must be blocked when a batch is at capacity, and what the overbooking policy is.
   - Whether admission status transitions beyond enrollment (`DROPPED`, `COMPLETED`) need approval, effective dates, or reasons recorded.
   - Whether a student changing course should close the previous admission automatically or leave it open.
   - Which roles may create enquiries versus only enroll, if those should differ.
2. **Academic Masters (Phase 2 follow-ups)**:
   - Course-code convention beyond the permissive shape currently enforced (`^[A-Z0-9][A-Z0-9-]*$`); whether codes should be segmented or length-limited by programme type.
   - Batch naming convention (for example `2026-A` versus `2026-2027-A`); codes are currently free text, unique per centre, course, and academic year. The student ID sequence depends on batch codes changing per year.
   - Whether more than one academic year may be `is_active` at the same time. No constraint enforces a single active year.
   - Whether batch dates must fall inside the linked academic year's date range. This is not constrained.
   - Fee templates per course or batch are not modelled on the academic masters; Phase 4 snapshots agreed amounts per admission instead (see [`PHASE4_FEE_PLANS.md`](PHASE4_FEE_PLANS.md)).
3. **Fees & Receipts (Phase 5)**:
   - Financial receipt numbering scope (centre-scoped vs portal-wide sequence); still open.
   - **Resolved in Phase 4:** A1 (INR 1,97,650 is GST-inclusive: taxable 1,67,500 + GST 30,150) and A2 (the four `COURSE` components must sum to INR 1,86,650; the split and dates are entered per plan). See [`PHASE4_FEE_PLANS.md`](PHASE4_FEE_PLANS.md) §11 and §14.
   - A3 — Who creates draft plans: the permissions matrix implements `ADMIN`/`MANAGEMENT` only. Confirm whether `ACCOUNTS` may also create or edit drafts.
   - A4 — Reassignment: the explicit authorised process for moving an existing admission to a corrected fee structure (who may trigger it, and whether it requires a recorded reason).
   - A5 — Partial-payment allocation policy (oldest-due-first vs staff-selected installment); still Phase 5.
   - Advance-payment, refund, and reversal policy.
4. **Production Hardening (Phase 6)**:
   - Resolve the six `check --deploy` warnings (HSTS, SSL redirect, secure session/CSRF cookies, non-console email backend, `DEBUG=False`) in the production settings, not in local development.
   - Private document storage and backup/retention handling, once uploads are implemented.

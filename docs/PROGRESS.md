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
| **Phase 5** | **Payments, Allocations & Traceable Receipts** | Concurrency-safe payments, centre-scoped sequential receipt numbers (`GIIMS/{CENTRE_CODE}/{FYyy-yy}/{000001}`), FIFO-suggested staff-selected allocations, advance-credit lots, refunds, reversals, financial audit trail, and controlled fee-plan reassignment. | **Completed** | 2026-11-03 |
| **Phase 6** | **Ledgers, Reports, Audit & Deployment** | Student fee ledgers, printable HTML and server-generated PDF receipts with `RECEIPT_REPRINTED` audit logging, operational & financial reports with formula-safe CSV and PDF exports, financial audit trail viewer, live centre-scoped dashboard, and production security hardening. | **Completed** | 2026-11-03 |

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
4. **Enrollment & Enquiry Flow**:
   - `enroll_admission(admission, user)` service performs the `ENQUIRED` → `ENROLLED` transition atomically, records the actor and timestamp, and assigns the student ID.
   - Combined enquiry creation: `StudentEnquiryForm` + `AdmissionEnquiryForm` create a `Student` and its first `Admission` in one atomic transaction.
5. **Views, Lists & Filtering**:
   - `/students/` list and detail, `/admissions/` list and detail, plus enquiry creation and a POST-only enrollment endpoint.
6. **Admin & Tests**:
   - `StudentAdmin` and `AdmissionAdmin` with list displays, filters, search, and read-only audit fields; `StudentIdSequenceAdmin` is read-only.
   - 17 tests in `apps/students/tests.py`.

#### Verification Run Results
Executed from the repository root on 2026-11-02:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test` → `Found 157 test(s).`, `Ran 157 tests in 12.633s`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `All files formatted`

### Phase 4: Fee Plans & Installment Schedules
- **Status:** Completed
- **Date:** 2026-11-02

#### Confirmed Business Policies
- The GST rate is stored per fee plan; July 2026 Logistics & SCM uses 18%. The rate, GST amount, and total are snapshotted when the plan is approved and are immutable afterwards.
- `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` may apply due-date revisions; `ADMISSIONS` may only request them. Every change records the previous date, the revised date, a required reason, the applying user, and a timestamp.
- Approved plans are immutable, while drafts remain editable. A correction creates a new plan version that preserves the old version and its audit history.
- Every plan carries six components: the application charge of INR 1,000 and the admission charge of INR 10,000 are stage-based with no calendar date, while the four course installments carry suggested dates.
- Due dates are target dates only. Overdue is a derived calculation against an explicit `as_of` date, not an enforcement gate.
- No discount, waiver, scholarship, or concession module exists.

#### Design Reference
- [PHASE4_FEE_PLANS.md](PHASE4_FEE_PLANS.md) — canonical Phase 4 design.

#### Verification Run Results
Executed from the repository root on 2026-11-02 against PostgreSQL:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test apps.fees` → `Ran 145 tests`, `OK`
- `uv run python manage.py test` → `Ran 302 tests`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `105 files already formatted`
- `npm run build` → HTMX/Alpine assets copied; Tailwind CSS v4 minified in 52ms

### Phase 5: Payments, Allocations & Traceable Receipts
- **Status:** Completed
- **Date:** 2026-11-03

#### Confirmed Business Policies
- **A3 Fee Plan Roles**: `ADMIN` and `MANAGEMENT` create, edit, approve, cancel, and activate fee plans. `ACCOUNTS` may create and edit draft plans and create new draft versions, but cannot approve or cancel plans. `ADMISSIONS` remains read-only on fee plans.
- **A4 Controlled Fee-Plan Reassignment**: `ACCOUNTS`, `ADMIN`, and `MANAGEMENT` may request reassignment to a draft plan version with a mandatory reason (minimum 10 characters); only `ADMIN` and `MANAGEMENT` may approve or reject.
- **A5 Staff-Selected Allocation with FIFO Suggestion**: `record_payment()` supports staff-selected explicit allocations, an automatic FIFO suggestion (`allocations=None`), or retaining the full payment as advance credit (`allocations=[]`).
- **Centre-Scoped Receipt Numbering**: Format `GIIMS/{CENTRE_CODE}/{FYyy-yy}/{000001}`, using the Indian financial year (`1 April`–`31 March`) derived from `Payment.paid_on` and a row-locked `ReceiptSequence` counter per `(centre, financial_year)`.
- **Advance Credit, Refunds & Reversals**: `AdvanceCredit` lots track `original_amount` and `remaining_amount` and are consumed oldest-first by `apply_advance_credit()`. Refunds (`request_refund` / `decide_refund`) and payment reversals (`request_payment_reversal` / `decide_payment_reversal`) are requested by `ACCOUNTS`/`ADMIN`/`MANAGEMENT` and decided by `ADMIN`/`MANAGEMENT`.
- **Multi-Centre Semantics**: `Payment.centre` is the collection centre and is stored separately from `Payment.admission.centre`.

#### Design Reference
- [PHASE5_PAYMENTS.md](PHASE5_PAYMENTS.md) — canonical Phase 5 design and implementation contract.

#### Verification Run Results
Executed from the repository root on 2026-11-03:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test` → `501 tests` (`apps.payments` + `apps.fees` + Phases 1–3)
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `135 files already formatted`
- `npm run build` → HTMX/Alpine assets copied; Tailwind CSS v4 minified

### Phase 6: Ledgers, Printable/PDF Receipts, Reports, Audit Trail & Deployment Hardening
- **Status:** Completed
- **Date:** 2026-11-03

#### Confirmed Business Policies
- **Export Formats**: Every operational and financial report supports print-friendly HTML views, formula-safe CSV exports (`apps.core.exporters.build_csv_response`), and server-generated `%PDF-1.4` binary downloads (`apps.core.exporters.build_pdf_response`).
- **Role Access Matrix**:
  - `ADMIN`, `MANAGEMENT`, and `ACCOUNTS` have access to all financial reports, the append-only financial audit trail, and all CSV/PDF exports within their accessible centre scope.
  - `ADMISSIONS` can access the Reports Hub (`/reports/`), the Student & Admission Register (`/reports/admissions/`), and read-only Student Fee Ledgers (`/reports/ledger/` and `/reports/ledger/<admission_id>/`) for accessible centres, and receives `403 Forbidden` on financial reports and exports.
  - Cross-centre admission and payment IDs resolve to `404 Not Found` for scoped users.
- **Receipt Reprint Auditing**: Opening the printable receipt view (`/payments/<pk>/receipt/`) or downloading the PDF receipt (`/payments/<pk>/receipt/pdf/`) calls `log_receipt_reprint()`, which records an append-only `RECEIPT_REPRINTED` `FinancialAuditEvent` without creating a duplicate payment or changing `Receipt.number`. Reversed receipts display a prominent `REVERSED / VOID` banner in both HTML and PDF.

#### Implemented Features
1. **CSV & Zero-Dependency PDF Exporter (`apps/core/exporters.py`)**:
   - `sanitize_csv_cell` neutralizes spreadsheet formula injection (`=`, `+`, `-`, `@`, `\t`, `\r`, and leading-whitespace variants) while preserving numeric `Decimal` values (including negative decimals).
   - `build_pdf_document` and `PdfTableSection` generate multi-page `%PDF-1.4` documents using standard Type1 `Helvetica` and `Helvetica-Bold` fonts with zero native external dependencies.
2. **Printable HTML & Server-Generated PDF Receipts (`apps/payments/`)**:
   - Added `RECEIPT_REPRINTED` to `FinancialAuditEvent.Event` (`apps/payments/migrations/0002_alter_financialauditevent_event_type.py`).
   - Added `log_receipt_reprint` in `apps/payments/services.py` and `ReceiptPrintView` / `ReceiptPdfView` in `apps/payments/views.py`.
   - Added printable receipt template `templates/payments/receipt_print.html` and wired links from `templates/payments/payment_detail.html`.
3. **Student Fee Ledgers & Reporting Suite (`apps/reports/`)**:
   - `build_student_ledger`: complete as-of ledger snapshot for an admission (plan versions, installment schedule with original vs effective due dates, payments, allocations, advance credit lots, refunds, reversals, reassignments, due-date revisions, and financial audit events).
   - `admissions_register_queryset`: centre-scoped Student & Admission Register with status, centre, course, batch, academic year, date-range, and free-text filters.
   - `build_outstanding_report` & `build_overdue_installments_report`: student-wise and aggregate billed, paid, outstanding, overdue, not-yet-due, undated stage-based, and available credit totals as of any chosen date, using prefetched rows to avoid N+1 queries.
   - `build_collection_report`: period-wise payment and receipt register with separate `by_collection_centre` (`Payment.centre`) and `by_admission_centre` (`Payment.admission.centre`) breakdowns, daily totals, and payment-mode summaries.
   - `build_adjustments_report`: centre-scoped reversals, refunds, and fee-plan reassignments report.
   - `audit_events_queryset`: filterable append-only `FinancialAuditEvent` viewer and exporter.
   - 9 templates under `templates/reports/` and 21 named URL routes under `/reports/`.
4. **Live Administrative Dashboard (`config/views.py`, `templates/dashboard.html`, `templates/base.html`)**:
   - Replaced static demo mockup numbers on `/` with live centre-scoped metrics from `dashboard_summary(request.user)` (`total_admissions`, `enrolled_admissions`, `enquired_admissions`, `month_collection`, `month_receipt_count`, `total_outstanding`, `overdue_outstanding`, `available_credit`, pending approval queues, `recent_payments`, and `recent_admissions`).
   - Wired Quick Action buttons and sidebar navigation links for Reports Hub, Student Fee Ledgers, Outstanding & Overdue, Collection Summary, and Financial Audit Trail.
5. **Production Security Hardening (`config/settings.py`, `.env.example`)**:
   - Enforced a 50+ character non-insecure `DJANGO_SECRET_KEY` when `DEBUG=False`.
   - Configured `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SECURE_HSTS_SECONDS=31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY`, `X_FRAME_OPTIONS="DENY"`, `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, and non-console SMTP `MAILERS` default when `DEBUG=False`.
6. **Tests**:
   - Added 4 exporter security/PDF tests in `apps/core/tests.py` and 9 integration tests in `apps/reports/tests/test_reports_and_receipts.py` covering receipt reprints/PDFs, void banners, ledger accuracy, role/centre access enforcement, cross-centre collection vs admission centre reporting, adjustments reporting, CSV formula sanitization, PDF generation, and live dashboard rendering (514 total tests across the repository).

#### Verification Run Results
Executed from the repository root on 2026-11-03:
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `DJANGO_DEBUG=False DJANGO_SECRET_KEY=<50+ char secret> uv run python manage.py check --deploy` → `System check identified no issues (0 silenced).`
- `uv run python manage.py makemigrations --check --dry-run` → `No changes detected`
- `uv run python manage.py test apps.core apps.reports` → `Ran 17 tests in 18.266s`, `OK`
- `uv run ruff check .` → `All checks passed!`
- `uv run ruff format --check .` → `144 files already formatted`
- `npm run build` → `HTMX and Alpine.js copied to static/dist/js/`, `tailwindcss v4.3.3 Done in 220ms`

---

## Deferred / Future Enhancements

1. **Private Student Document Uploads (`StudentDocument`)**:
   - Deferred by agreement. When implemented, store uploads outside public static directories, validate size/extension/content, and serve only through an authenticated, centre-scoped view.
2. **Admissions & Academic Master Follow-Ups**:
   - Batch capacity enforcement at enrollment (`Batch.capacity` is currently informational).
   - Whether `DROPPED` / `COMPLETED` transitions require approval or effective dates.
3. **Additional Financial Policy Refinements**:
   - Cheque/DD clearance state machine if uncleared instruments need a holding state prior to posting.
   - Optional four-eyes rule preventing an `ADMIN`/`MANAGEMENT` user from approving their own refund, reversal, or reassignment request.

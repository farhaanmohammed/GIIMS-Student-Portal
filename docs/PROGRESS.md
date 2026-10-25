# GIIMS Student Portal — Project Progress Tracker

This document tracks implementation progress across phases, verification runs, test results, and architectural decisions for the GIIMS Student Portal.

---

## High-Level Milestone Tracker

| Phase | Milestone | Scope | Status | Completed Date |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | **Foundation, Auth & Centre Scoping** | PostgreSQL setup, custom `User` model, role-based permissions, centre-level scoping, login/logout, Tailwind CSS v4, HTMX, Alpine.js, test suite & Ruff linter. | **Completed** | 2026-10-24 |
| **Phase 2** | **Academic Masters & Structure** | `AcademicYear`, `Course`, `Batch` masters, centre-scoped batch management, admin registration, role-gated CRUD/HTMX screens. | **Completed** | 2026-10-25 |
| **Phase 3** | **Student & Admission Management** | Durable `Student` profile, `Admission` lifecycle (`ENQUIRED` to `ENROLLED`), document uploads, centre-scoped views. | **Up Next** | In Planning |
| **Phase 4** | **Fee Plans & Installment Schedules** | Custom admission fee agreements, installment schedules, due dates, discount/waiver approvals. | Planned | — |
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
   - No derived seat counters or enrolment totals are stored; capacity enforcement is deferred to Phase 3 admissions.
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
- 2 regression tests added during integration in `apps/accounts/tests.py`: `test_can_access_centre_accepts_primary_key_and_code` and `test_can_access_centre_rejects_inactive_centre_by_pk_and_code`.

Counts were taken from the test definitions in those files; the totals reconcile with the reported 140-test run.

#### Known Limitations
- All-centres users (`ADMIN`, `MANAGEMENT`, superusers) see batches belonging to inactive centres, because `Batch.objects.for_user()` returns the unnarrowed queryset for that group. This is deliberate privileged-administration behaviour.
- `BatchForm` centre choices come from `get_accessible_centres()`, which returns active centres only, so editing a batch whose centre was later deactivated requires a change to that whitelist.
- CSRF is enforced by middleware and rendered tokens are asserted in the template tests, but Django's test client bypasses CSRF validation, so an end-to-end CSRF-protected POST is not covered by automated tests.
- A cross-centre batch update returning 404 is only covered indirectly: `ADMIN`/`MANAGEMENT` have all-centres access by design, so the 404 branch is exercised by list-scoping and form centre-queryset tests rather than a direct cross-centre write test.

---

## Open Decisions & Items to Confirm Before Next Phases

1. **Academic Masters (Phase 2 follow-ups)**:
   - Course-code convention beyond the permissive shape currently enforced (`^[A-Z0-9][A-Z0-9-]*$`); whether codes should be segmented or length-limited by programme type.
   - Batch naming convention (for example `2026-A` versus `2026-2027-A`); codes are currently free text, unique per centre, course, and academic year.
   - Whether more than one academic year may be `is_active` at the same time. No constraint enforces a single active year.
   - Whether batch dates must fall inside the linked academic year's date range. This is not constrained.
   - Capacity and overbooking policy. `capacity` is nullable with no default, and admissions-time enforcement is deferred to Phase 3.
   - Fee templates per course or batch are deferred to Phase 4, pending fee-type and discount decisions.
2. **Admissions & Receipts (Phases 3-5)**:
   - Admission number format and centre-specific prefix rules.
   - Financial receipt numbering scope (centre-scoped vs portal-wide sequence).
3. **Production Hardening (Phase 6)**:
   - Resolve the six `check --deploy` warnings (HSTS, SSL redirect, secure session/CSRF cookies, non-console email backend, `DEBUG=False`) in the production settings, not in local development.

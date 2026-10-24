# GIIMS Student Portal — Project Progress Tracker

This document tracks implementation progress across phases, verification runs, test results, and architectural decisions for the GIIMS Student Portal.

---

## High-Level Milestone Tracker

| Phase | Milestone | Scope | Status | Completed Date |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | **Foundation, Auth & Centre Scoping** | PostgreSQL setup, custom `User` model, role-based permissions, centre-level scoping, login/logout, Tailwind CSS v4, HTMX, Alpine.js, test suite & Ruff linter. | **Completed** | 2026-10-24 |
| **Phase 2** | **Academic Masters & Structure** | `AcademicYear`, `Course`, `Batch` models, fee templates, batch capacity, centre scoping. | **Up Next** | In Planning |
| **Phase 3** | **Student & Admission Management** | Durable `Student` profile, `Admission` lifecycle (`ENQUIRED` to `ENROLLED`), document uploads, centre-scoped views. | Planned | — |
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

---

## Open Decisions & Items to Confirm Before Next Phases

1. **Academic Masters (Phase 2)**:
   - Format for Course codes (e.g. `BBA-LSC`, `AVM-01`).
   - Batch naming convention (e.g. `2024-2027-A`).
2. **Admissions & Receipts (Phases 3-5)**:
   - Admission number format and centre-specific prefix rules.
   - Financial receipt numbering scope (centre-scoped vs portal-wide sequence).

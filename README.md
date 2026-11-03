# GIIMS Student Portal

A web-based student admission and fee-management portal for GIIMS, built
with Django, PostgreSQL, HTMX, and Alpine.js.

> **Project status:** Phases 1–6 implemented. Phase 1 delivered settings, PostgreSQL, the custom `User` model, centre scoping, authentication, Tailwind CSS v4, HTMX, Alpine.js, Ruff, and the test suite. Phase 2 delivered academic years, courses, and centre-scoped batch management under `/academics/`, with role-gated writes. Phase 3 delivered student master, admission lifecycle (`ENQUIRED` → `ENROLLED`), student ID generation on enrollment (`TVM-ASP-AD_001` format), combined enquiry flow, and centre-scoped views under `/students/` and `/admissions/`. Phase 4 delivered versioned fee plans, the per-plan GST snapshot, six-component installment schedules, and the due-date request/approval workflow under `/fees/`. Phase 5 delivered payments, concurrency-safe centre-scoped receipt numbering, FIFO-suggested staff-selected allocations, advance-credit lots, refunds, reversals, and controlled fee-plan reassignment under `/payments/` and `/fees/reassignments/`. Phase 6 delivered student fee ledgers, printable HTML and server-generated PDF receipts with `RECEIPT_REPRINTED` audit logging, operational and financial reports with formula-safe CSV and PDF exports under `/reports/`, the append-only financial audit trail viewer, live centre-scoped dashboard KPIs, and production deployment security hardening.

## What the application does

-   Capture student details and admission/joining information.
-   Manage centres, academic years, courses, and batches. Academic-year,
    course, and batch masters are available at `/academics/`
    (`ADMIN` and `MANAGEMENT` can create, edit, and toggle them; all
    authenticated roles can view them).
-   Create admission-specific fee plans and installment schedules.
-   Record payments and generate traceable, printable HTML and PDF receipts.
-   Show student fee ledgers and calculate outstanding/overdue amounts as of any chosen date.
-   Provide student/admission register, outstanding, overdue, collection, adjustment, and financial audit trail reports with CSV and PDF exports under `/reports/`.
-   Enforce role-based and centre-level access across every page, HTMX endpoint, receipt download, and export.
-   Maintain append-only audit history for sensitive financial changes and receipt reprints.

See [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) for scope,
architecture, business rules, and engineering standards.
Implementation milestones, completed deliverables, and test records are tracked in [`docs/PROGRESS.md`](docs/PROGRESS.md).
The Phase 4 fee plan, installment schedule, and due-date contract is in [`docs/PHASE4_FEE_PLANS.md`](docs/PHASE4_FEE_PLANS.md). The Phase 5 payment, receipt, allocation, credit, refund, reversal, and reassignment contract is in [`docs/PHASE5_PAYMENTS.md`](docs/PHASE5_PAYMENTS.md). Both are implemented and migrated.
AI coding-agent instructions are in [`AGENTS.md`](AGENTS.md).

## Technology

-   Python
-   Django
-   PostgreSQL
-   Django Templates
-   HTMX
-   Alpine.js
-   Tailwind CSS (compiled for production)
-   Django TestCase suite (`python manage.py test`)
-   Ruff for linting and formatting

## Repository layout

``` text
.
├── AGENTS.md
├── README.md
├── docs/
│   └── PROJECT_SPEC.md
├── config/
├── apps/
│   ├── core/
│   ├── accounts/
│   ├── centres/
│   ├── academics/
│   ├── students/
│   ├── admissions/
│   ├── fees/
│   ├── payments/
│   └── reports/
├── templates/
├── static/
├── pyproject.toml
└── .env.example
```

## Local setup

### 1. Prerequisites

Install: - A supported Python version compatible with the selected
Django release. - PostgreSQL. - Git. - Node.js for the Tailwind CSS v4 build pipeline.

### 2. Create and activate a virtual environment

Linux/macOS:

``` bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows PowerShell:

``` powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 3. Configure environment

Create a local `.env` file based on `.env.example`. Never commit `.env`.

Expected configuration includes values similar to:

``` dotenv
DJANGO_SECRET_KEY=replace-with-a-long-random-local-secret-at-least-50-chars
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=giims_db
DB_USER=giims_app
DB_PASSWORD=change-me
DB_HOST=localhost
DB_PORT=5432
TIME_ZONE=Asia/Kolkata
```

These are examples, not credentials. The application validates
required settings at startup. Production must use a separate random secret of at least 50 characters and
`DJANGO_DEBUG=False` (which automatically enables `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS=31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_PROXY_SSL_HEADER`, and the SMTP email backend).

### 4. Create the PostgreSQL database

Example commands (adapt the role/database names to your environment):

``` sql
CREATE ROLE giims_app WITH LOGIN PASSWORD 'use-a-local-secret';
CREATE DATABASE giims_db OWNER giims_app;
```

Use a least-privilege application role. Do not use the PostgreSQL
superuser as the application's database account.

### 5. Install dependencies

``` bash
uv sync
npm install
npm run build
```

### 6. Run migrations and create an administrator

``` bash
uv run python manage.py check
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

### 7. Start the development server

``` bash
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

Do not use Django's development server for production.

## Common development commands

``` bash
# Build frontend assets (Tailwind CSS v4, HTMX, Alpine.js)
npm run build

# Watch Tailwind CSS for changes during template editing
npm run watch:css

# Run Django development server
uv run python manage.py runserver

# Run tests
uv run python manage.py test

# Lint
uv run ruff check .

# Format check
uv run ruff format --check .

# Django deployment/security checks
uv run python manage.py check --deploy

# Review migrations
uv run python manage.py makemigrations --check --dry-run
```

Run migrations in a controlled deployment process, not automatically on every production web-worker startup.

## Core engineering rules

-   Use a custom Django user model from the start.
-   Enforce permissions and centre scoping server-side.
-   Use `DecimalField` for money.
-   Keep payment posting, allocations, reversals, and receipt issuance
    in tested service functions.
-   Use database transactions and concurrency-safe unique numbering for
    financial writes.
-   Never hard-delete posted payments.
-   Do not trust totals or IDs supplied by the browser.
-   Protect state-changing requests with CSRF.
-   Keep uploaded student documents private and permission-checked.
-   Use synthetic student data for development and tests.
-   Back up PostgreSQL and test restoration.
-   Read `AGENTS.md` and `docs/PROJECT_SPEC.md` before making
    substantial changes.

## Development milestones

1.  **Done (Phase 1):** Project bootstrap, settings, database,
    authentication, and access model.
2.  **Done (Phase 2):** Centre, academic year, course, and batch masters.
3.  **Done (Phase 3):** Student master and admission workflow with student ID generation on enrollment.
4.  **Done (Phase 4):** Versioned fee plans with a per-plan GST snapshot,
    six components per plan including stage-based application and admission
    charges, approved-plan immutability, and the due-date request/approval
    workflow. No discount or waiver module.
5.  **Done (Phase 5):** Payments, staff-selected allocations with FIFO
    suggestions, concurrency-safe centre-scoped receipt numbering, advance
    credit, refunds, reversals, audit events, and controlled fee-plan
    reassignment.
6.  **Done (Phase 6):** Student fee ledgers, printable HTML and server-generated PDF receipts with reprint audit logging, operational/financial reports with CSV and PDF exports, financial audit trail viewer, live centre-scoped dashboard, and production deployment hardening.

## Security note

This system holds personal and financial records. Do not put real
student data, passwords, API keys, private documents, or production
database dumps in Git, prompts, screenshots, or test fixtures. Configure
backups, access controls, HTTPS, secure cookies, and production secrets
before deployment.

## Project decisions to confirm

Resolved in Phase 4: the GST basis (INR 1,97,650 is GST-inclusive:
taxable value 1,67,500 plus GST 30,150), the six-component plan structure
(application INR 1,000 and admission INR 10,000 stage-based, four dated
`COURSE` components summing to INR 1,86,650), approved-plan versioning,
and the due-date request/approval roles.

Resolved in Phase 5: centre-scoped receipt format
`GIIMS/{CENTRE_CODE}/{FYyy-yy}/{000001}`, FIFO-suggested staff-selected
allocation with advance credit for excess, `ACCOUNTS` draft plan editing
(approval stays with `ADMIN`/`MANAGEMENT`), mandatory-reason reassignment
approval by `ADMIN`/`MANAGEMENT`, and refund/reversal approval roles.

Resolved in Phase 6: CSV (with formula-injection neutralization) and
server-generated PDF exports alongside printable HTML views; role access
matrix (`ADMIN`, `MANAGEMENT`, and `ACCOUNTS` access all financial reports,
audit trails, and exports; `ADMISSIONS` views the Reports Hub, Admission
Register, and read-only Student Fee Ledgers for accessible centres); and
append-only `RECEIPT_REPRINTED` audit logging on receipt print/PDF downloads.

Still to confirm before future enhancements:

-   The canonical payment-mode list, cheque/DD clearance behaviour, and
    whether a refund can ever bypass the advance-credit ledger.
-   Whether `ADMIN`/`MANAGEMENT` may approve their own refund, reversal, or
    reassignment request (four-eyes approval).
-   Whether reversal should remain blocked after an approved reassignment.
-   Private student document storage (`StudentDocument`) and backup retention policy.

## Contributing

Keep changes small and reviewable. Add tests with new behavior, inspect
migrations, run quality checks, and update documentation. Report the
commands actually run and their results; never claim a check passed
without running it.

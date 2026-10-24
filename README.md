# GIIMS Student Portal

A web-based student admission and fee-management portal for GIIMS, built
with Django, PostgreSQL, HTMX, and Alpine.js.

> **Project status:** Phase 1 Foundation implemented (Settings, PostgreSQL,
> Custom User model, Centre scoping, Authentication, Tailwind CSS v4, HTMX,
> Alpine.js, Ruff lint/format, and test suite). Subsequent domain modules
> are implemented in iterative phases following `docs/PROJECT_SPEC.md`.

## What the application will do

-   Capture student details and admission/joining information.
-   Manage centres, academic years, courses, and batches.
-   Create admission-specific fee plans and installment schedules.
-   Record payments and generate traceable receipts.
-   Show student fee ledgers and calculate outstanding/overdue amounts.
-   Provide student, admission, collection, and outstanding reports.
-   Enforce role-based and centre-level access.
-   Maintain audit history for sensitive changes.

See [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) for scope,
architecture, business rules, and engineering standards.
Implementation milestones, completed deliverables, and test records are tracked in [`docs/PROGRESS.md`](docs/PROGRESS.md).
AI coding-agent instructions are in [`AGENTS.md`](AGENTS.md).

## Technology

-   Python
-   Django
-   PostgreSQL
-   Django Templates
-   HTMX
-   Alpine.js
-   Tailwind CSS (compiled for production)
-   pytest/pytest-django or one consistently selected Django testing
    approach
-   Ruff for linting and formatting

Choose compatible stable versions during bootstrap and pin them in
`pyproject.toml` or the chosen dependency lock workflow.

## Repository layout

``` text
.
├── AGENTS.md
├── README.md
├── docs/
│   └── PROJECT_SPEC.md
├── config/
├── apps/
├── templates/
├── static/
├── tests/
├── pyproject.toml
└── .env.example
```

The actual tree may evolve as implementation begins. Do not create
placeholder apps solely to match this diagram.

## Local setup

The following is the intended setup flow. Exact commands may need
adjustment after the initial Django project and dependency files are
created.

### 1. Prerequisites

Install: - A supported Python version compatible with the selected
Django release. - PostgreSQL. - Git. - Node.js only if using a
Node-based Tailwind build pipeline.

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

Expected configuration will include values similar to:

``` dotenv
DJANGO_SETTINGS_MODULE=config.settings.local
DJANGO_SECRET_KEY=replace-with-a-long-random-local-secret
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://giims_app:change-me@localhost:5432/giims
TIME_ZONE=Asia/Kolkata
```

These are examples, not credentials. The application must validate
required settings at startup. Production must use a separate secret and
`DEBUG=False`.

### 4. Create the PostgreSQL database

Example commands (adapt the role/database names to your environment):

``` sql
CREATE ROLE giims_app WITH LOGIN PASSWORD 'use-a-local-secret';
CREATE DATABASE giims OWNER giims_app;
```

Use a least-privilege application role. Do not use the PostgreSQL
superuser as the application's database account.

### 5. Install dependencies

Once the project dependency files have been created:

``` bash
python -m pip install -e ".[dev]"
```

If the project uses a different dependency manager, follow the committed
lockfile and document the canonical command here.

### 6. Run migrations and create an administrator

``` bash
python manage.py check
python manage.py migrate
python manage.py createsuperuser
```

### 7. Start the development server

``` bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

Do not use Django's development server for production.

## Common development commands

Use the commands configured by the repository; these are the intended
defaults:

``` bash
# Build frontend assets (Tailwind CSS v4, HTMX, Alpine.js)
npm run build

# Watch Tailwind CSS for changes during template editing
npm run watch:css

# Run Django development server
uv run python manage.py runserver

# Run tests
pytest

# Lint
ruff check .

# Format check
ruff format --check .

# Django deployment/security checks
python manage.py check --deploy

# Review migrations
python manage.py makemigrations --check --dry-run
```

If a command is not configured yet, configure it before treating it as a
required project check. Run migrations in a controlled deployment
process, not automatically on every production web-worker startup.

## Core engineering rules

-   Use a custom Django user model from the start if the project
    requires one.
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

1.  Project bootstrap, settings, database, authentication, and access
    model.
2.  Centre, academic year, course, and batch masters.
3.  Student master and admission workflow.
4.  Fee plans, installments, payments, allocations, and receipts.
5.  Student ledger and collection/outstanding reports.
6.  Audit trail, security review, tests, and deployment.

## Security note

This system will hold personal and financial records. Do not put real
student data, passwords, API keys, private documents, or production
database dumps in Git, prompts, screenshots, or test fixtures. Configure
backups, access controls, HTTPS, secure cookies, and production secrets
before deployment.

## Project decisions to confirm

Before implementing the related workflows, confirm: - Centre codes and
student/admission ID format. - Receipt number scope and format. -
Academic year boundaries. - Fee types, discount/waiver permissions, and
approval workflow. - Partial-payment allocation policy. -
Advance-payment, refund, and reversal policies. - Roles and cross-centre
visibility. - Hosting, private document storage, and backup retention.

## Contributing

Keep changes small and reviewable. Add tests with new behavior, inspect
migrations, run quality checks, and update documentation. Report the
commands actually run and their results; never claim a check passed
without running it.

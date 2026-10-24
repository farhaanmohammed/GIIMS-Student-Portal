# GIIMS Student Portal --- Project Brief & Engineering Standards

**Document purpose:** Source of truth for AI-assisted development in
Antigravity\
**Project type:** Multi-centre student admissions and fee-management web
application\
**Status:** Initial product and technical specification\
**Primary users:** Administrators, admissions staff, accounts staff,
management

------------------------------------------------------------------------

## 1. Product vision

Build a secure, maintainable, responsive web portal for GIIMS to manage
student admissions from joining day through fee collection and financial
reporting.

The first release must prioritize reliable student records, traceable
financial transactions, accurate outstanding balances, role-based
access, and useful operational reports. Keep the architecture simple: a
Django monolith with server-rendered templates, HTMX, Alpine.js, and
PostgreSQL. Do not introduce a separate SPA or microservices without a
documented requirement.

## 2. Version 1 scope

### In scope

1.  Authentication, user management, roles, permissions, and
    centre-level access.
2.  Centre, academic year, course, and batch masters.
3.  Student master and admission/joining form.
4.  Admission record linked to student, course, batch, centre, and
    academic year.
5.  Fee plan, discounts/adjustments, and installment schedule.
6.  Payment entry with payment method, transaction reference, receipt,
    and allocation to dues.
7.  Student fee ledger and printable/downloadable receipt.
8.  Student-wise and aggregate outstanding reports.
9.  Period-wise collection reports with filters and exports.
10. Audit trail, automated tests, deployment configuration, and
    backup/restore guidance.

### Out of scope for Version 1 unless explicitly approved

-   Attendance, examinations, marks, certificates, placement, payroll,
    LMS.
-   Native mobile apps.
-   Online payment gateway integration.
-   WhatsApp/SMS automation.
-   Separate frontend SPA or public API.
-   Complex accounting/general-ledger functionality.

Design boundaries so future modules can be added without rebuilding the
core.

## 3. Technology stack

Use supported, mutually compatible stable releases at implementation
time and pin exact versions in dependency files.

-   **Language:** Python, with type hints for application/service code.
-   **Backend:** Django, using Django ORM, Forms, authentication,
    permissions, templates, and migrations.
-   **Database:** PostgreSQL.
-   **Frontend:** Django Templates + HTMX + Alpine.js.
-   **CSS:** Tailwind CSS, compiled for production; avoid relying on a
    runtime CDN in production.
-   **Testing:** pytest + pytest-django, or Django's test framework if
    the team standardizes on it; use one primary approach consistently.
-   **Code quality:** Ruff for linting/formatting; mypy or Pyright only
    if type checking is adopted consistently.
-   **Configuration:** environment variables; use a validated settings
    approach.
-   **Production serving:** a production WSGI/ASGI server and HTTPS
    reverse proxy/platform.
-   **Static and uploaded media:** separate static assets from user
    uploads; use object storage in production where appropriate.
-   **CI:** automated tests, linting, migration checks, and
    dependency/security checks.

Do not assume a package's latest version is compatible with the selected
Django/Python version. Verify compatibility before adding dependencies.

## 4. Architecture principles

### 4.1 Django monolith

Use a modular monolith organized by business domain. Keep request
handling thin:

-   **Models:** persistence, relationships, field constraints, and
    simple domain invariants.
-   **Forms:** input parsing and user-facing validation.
-   **Views:** authorization, request/response orchestration, and
    rendering.
-   **Services:** multi-model workflows and business operations,
    especially payments, reversals, allocations, and admission creation.
-   **Selectors/query helpers:** reusable read queries and report query
    logic when useful.
-   **Templates/partials:** server-rendered UI and HTMX fragments.
-   **Tests:** model, service, view, permission, and end-to-end workflow
    tests.

Do not place complex business logic in templates or duplicate it across
views. Avoid an unnecessary repository layer that merely wraps every ORM
call.

### 4.2 Suggested repository structure

``` text
giims/
├── manage.py
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── AGENTS.md                 # AI coding instructions for the repository
├── docs/
│   ├── PROJECT_SPEC.md       # Product and engineering source of truth
│   ├── DATA_MODEL.md
│   ├── SECURITY.md
│   └── OPERATIONS.md
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── local.py
│   │   └── production.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── core/
│   ├── accounts/
│   ├── centres/
│   ├── academics/
│   ├── students/
│   ├── admissions/
│   ├── fees/
│   ├── receipts/
│   ├── reports/
│   └── audit/
├── templates/
│   ├── base.html
│   ├── components/
│   └── ...
├── static/
│   └── src/
└── tests/                    # Cross-app and workflow tests if useful
```

This is a proposed structure, not a requirement to create empty apps
immediately. Create an app when there is a clear domain boundary. Keep
app-local templates/static files close to the app when that improves
ownership and discoverability.

### 4.3 Multi-centre readiness

Support multiple GIIMS centres in the data model from the beginning. A
user should only access records permitted by their role and centre
assignments. Centre scoping must be enforced server-side in every query
and mutation---not only by hiding UI controls.

Avoid database-per-centre or schema-per-centre for this use case unless
requirements materially change. A shared database with explicit centre
relationships is the default.

## 5. Domain model and data rules

The following are logical entities; names and exact relationships may be
refined during implementation.

-   `User`: authenticated account. Prefer a custom user model created at
    project initialization.
-   `Role`/groups and permissions: access control; use Django
    Groups/Permissions where appropriate.
-   `Centre`: branch/centre master.
-   `AcademicYear`: academic year and active state.
-   `Course`: course metadata and default pricing reference.
-   `Batch`: course intake, centre, start/end dates, and status.
-   `Student`: durable personal identity and contact details.
-   `Admission`: enrolment event connecting a student to course, batch,
    centre, and academic year.
-   `FeePlan`: agreed charges and discounts for an admission.
-   `FeeInstallment`: scheduled amount and due date.
-   `Payment`: payment transaction and receipt/payment metadata.
-   `PaymentAllocation`: allocation of a payment to one or more fee
    installments.
-   `FeeAdjustment`: authorized credit/debit/discount adjustment with
    reason and approver where required.
-   `PaymentReversal` or an equivalent explicit reversal record:
    correction trail for erroneous payments.
-   `AuditEvent`: security-sensitive and financial changes.
-   `StudentDocument`: metadata and secure reference to uploaded files.

### Data integrity requirements

-   Use `DecimalField` for all monetary values; never use binary
    floating-point for money.
-   Use explicit currency handling. If only INR is supported in Version
    1, document INR as the operating currency.
-   Use foreign keys with deliberate `on_delete` behavior. Do not
    cascade-delete financial records or admitted students casually.
-   Add database constraints and indexes for genuine invariants and
    common filters.
-   Use unique constraints for stable identifiers, scoped as needed:
    student/admission number, receipt number, centre code, and relevant
    external transaction identifiers.
-   Use `created_at`, `updated_at`, and `created_by`/`updated_by` where
    operationally useful.
-   Use explicit statuses and choices; avoid magic strings.
-   Store dates consistently and enable timezone-aware datetimes.
-   Avoid storing redundant balances as independently editable values.
-   Preserve admission snapshots of agreed course pricing/discounts so
    future changes to a course's default fee do not rewrite historical
    agreements.
-   Define how a student can have multiple admissions and how
    re-admission is handled.
-   Keep personal data minimal and collect only what the business needs.

### Outstanding balance definition

Document the precise accounting rule. A typical admission-level balance
is:

`Net charges + approved debit adjustments - posted payments/credits allocated to that admission`

The installment-level outstanding is the installment amount plus
applicable adjustments minus its allocations. Reports must distinguish
at least: - total outstanding, - overdue outstanding (unpaid amount with
due date before the report date), - not-yet-due outstanding.

Do not infer that every unpaid amount is overdue. Handle unallocated
payments and overpayments explicitly rather than silently dropping them.

## 6. Financial workflow requirements

### 6.1 Record payment

Implement a dedicated service such as `record_payment(...)`. The service
must:

1.  Validate the actor's permissions and access to the relevant
    centre/admission.
2.  Validate amount, currency, method, payment date, and required
    references.
3.  Use `transaction.atomic()` for the complete write workflow.
4.  Prevent duplicate submissions using a server-generated idempotency
    key or another robust uniqueness mechanism.
5.  Lock the relevant records with `select_for_update()` when needed to
    prevent concurrent over-allocation or duplicate receipt numbering.
6.  Create the payment and its allocations consistently.
7.  Enforce that allocations cannot exceed the amount available to
    allocate, unless a documented credit/advance-payment design permits
    it.
8.  Create the receipt record/number safely under concurrency.
9.  Record actor and audit metadata.
10. Return a clear success/error result without exposing sensitive
    internals.

Do not trust totals, outstanding amounts, receipt numbers, permissions,
or student IDs supplied by the browser. Recalculate and validate on the
server.

### 6.2 Reversals and corrections

-   Posted payments must not be hard-deleted or silently edited.
-   Correct mistakes using a reversal/void workflow with reason, actor,
    timestamp, and permission checks.
-   A reversal must update the effective ledger/reporting position
    through explicit records.
-   Receipt numbering must remain traceable after voiding.
-   Refunds, reversals, discounts, and fee waivers are different
    business events; do not treat them as interchangeable.
-   Define approval requirements for discounts, refunds, reversals, and
    changes to an agreed fee plan.

### 6.3 Receipt numbering

-   Generate receipt numbers on the server, never in JavaScript.
-   Define whether numbering is global, per centre, or per financial
    year.
-   Enforce uniqueness in PostgreSQL.
-   Use a concurrency-safe mechanism; do not calculate the next number
    by counting rows.
-   Keep a receipt audit trail and support reprinting an existing
    receipt without creating a new payment.

### 6.4 Fee schedules

-   Allow multiple installments and configurable due dates.
-   Record the agreed fee plan for each admission.
-   Support approved scholarships/discounts and explainable adjustments.
-   Define how partial payments are allocated (oldest due first by
    default, or selected installment if the user has permission).
-   Keep allocation policy deterministic and tested.
-   Support advance/unallocated payments only if the workflow is
    explicitly defined.
-   Provide a clear distinction between billed, paid, allocated,
    adjusted, and outstanding amounts.

## 7. UI/UX standards

-   Use a consistent responsive admin layout with clear navigation and
    breadcrumbs.
-   Prioritize desktop data-entry efficiency while remaining usable on
    tablets and phones.
-   Use semantic HTML, labels, keyboard-accessible controls, visible
    focus states, and accessible error messages.
-   Use Django form validation and display field-level and non-field
    errors.
-   Use HTMX for server-rendered partial updates, search, filters,
    pagination, dependent selections, and table refreshes.
-   Use Alpine.js for lightweight local interactions: toggles, menus,
    modal state, tabs, and confirmation dialogs.
-   Keep business rules on the server. Client-side calculations are
    previews only.
-   Include loading indicators, empty states, success/error messages,
    and confirmation for sensitive actions.
-   Do not place sensitive personal or financial information in URLs.
-   Protect all state-changing operations with POST/appropriate HTTP
    methods and CSRF protection.
-   Avoid making an HTMX endpoint less secure than its full-page
    equivalent.
-   Use progressive enhancement where practical so core workflows do not
    depend on elaborate client-side state.
-   Ensure all report filters are preserved when navigating pages or
    exporting.

## 8. Security and privacy

Follow Django's current security guidance and OWASP web application
principles.

-   Use Django authentication and authorization; never implement
    password storage manually.
-   Create a custom user model at project start if needed; changing
    later is costly.
-   Use least privilege and deny access by default.
-   Enforce centre-level row scoping in the server-side query layer.
-   Apply CSRF protection to authenticated state-changing requests.
-   Escape user-generated content; avoid unsafe template rendering.
-   Validate uploads by size, extension, and actual content where
    possible; do not trust browser MIME types.
-   Store private student documents outside the public static directory.
    Serve them through authorized views or private object-storage URLs.
-   Do not commit `.env`, secrets, production data, student documents,
    or credentials.
-   Set secure production cookies, HTTPS, HSTS, allowed hosts, trusted
    origins, and appropriate proxy settings after deployment topology is
    known.
-   Keep `DEBUG=False` in production.
-   Use secure password policy and MFA for privileged accounts if
    feasible.
-   Log security events while avoiding passwords, tokens, full payment
    data, and unnecessary personal data.
-   Establish data retention, backup encryption, access review, and
    incident-response procedures.
-   Never use real student data in development fixtures or screenshots;
    use synthetic data.

## 9. Reports and exports

Version 1 reports:

1.  Student register with filters.
2.  Admission register by date, centre, course, batch, academic year,
    and status.
3.  Student fee ledger.
4.  Outstanding by student/admission.
5.  Overdue installment report as of a selected date.
6.  Collection by date range, centre, course, payment method, and
    collector.
7.  Daily collection summary and receipt register.
8.  Reversal/refund/adjustment report.

Requirements: - Use consistent definitions across dashboard and
exports. - Apply the same authorization and centre scoping to HTML, CSV,
XLSX, and PDF exports. - Use pagination for large lists and avoid N+1
queries with `select_related()`/`prefetch_related()` where
appropriate. - Use decimal-safe aggregation. - Include report filters,
generation time, and selected as-of date where relevant. - Do not
calculate aggregate reports by looping over every student in Python when
a suitable database aggregation can do the work. - Consider CSV formula
injection when exporting untrusted text to spreadsheet formats. - Keep
sensitive exports permission-controlled and audit their generation if
appropriate.

## 10. HTMX and Alpine.js conventions

-   Use Django templates as the source of truth for rendered HTML.
-   Keep reusable partials in a predictable template directory.
-   Prefer clear response conventions: full page for normal navigation,
    partial HTML for HTMX requests.
-   Use `request.htmx` only if the selected HTMX integration provides
    it; otherwise use a small, documented request-header helper.
-   Validate that HTMX requests receive the same authorization and CSRF
    protection as normal requests.
-   Avoid broad `hx-swap-oob` updates unless necessary and tested.
-   Do not put secrets or trusted authorization decisions in Alpine
    state.
-   Do not use Alpine.js to replace server-side validation.
-   Pin and document frontend dependencies; compile production assets
    and avoid unpinned external scripts.
-   Keep JavaScript minimal and modular.

## 11. Testing strategy

Tests are part of the feature, not a later cleanup task.

### Required coverage

-   Model constraints and relationships.
-   Form validation and invalid inputs.
-   Role and centre access for every sensitive workflow.
-   Admission creation and editing.
-   Fee-plan calculation and installment creation.
-   Full payment lifecycle: partial, exact, multiple installments,
    overpayment policy, duplicate submission, reversal, and receipt
    reprint.
-   Concurrent payment/receipt behavior where practical.
-   Outstanding and overdue calculations, including report as-of dates.
-   HTMX partial responses and normal page responses.
-   Export permissions and report totals.
-   Upload validation and access control.

### Quality gates

Before marking a task complete: - Automated tests pass. - Lint/format
checks pass. - Migrations are reviewed and apply cleanly. - No secrets
or real personal data were added. - Permissions and centre scoping are
verified. - The UI includes loading, empty, success, and error states
where applicable. - Documentation is updated if behavior, setup, or
architecture changed.

Use factories or fixtures with synthetic data. Do not make tests depend
on production data or external services.

## 12. Development workflow

1.  Read `AGENTS.md`, `README.md`, and `docs/PROJECT_SPEC.md` before
    changing the project.
2.  Inspect the existing repository and reuse established conventions.
3.  For significant changes, outline the plan and affected files before
    editing.
4.  Implement one vertical slice at a time: model + migration +
    service + view + template + tests.
5.  Keep changes small and reviewable.
6.  Run focused tests, then the full relevant test suite.
7.  Inspect migrations and generated diffs.
8.  Summarize files changed, behavior added, commands run, test results,
    and remaining risks.
9.  Never claim tests passed unless they were actually run.
10. Ask before destructive operations, deleting user data, changing
    production infrastructure, or adding expensive/external services.

## 13. Definition of done

A feature is complete only when: - Its business rules are documented and
enforced server-side. - Permissions and centre scoping are applied. -
Database integrity is maintained. - Success, validation failure, and
exceptional cases are handled. - Tests cover the main workflow and
important edge cases. - UI is responsive and accessible. - No sensitive
information is leaked through logs, URLs, errors, or exports. -
Documentation and sample configuration are updated.

## 14. Open decisions to confirm with GIIMS

Do not invent these business policies. Record decisions before
implementing the affected workflow.

-   Official centre list and centre codes.
-   Student ID/admission number format and uniqueness scope.
-   Receipt number format and whether numbering is per centre/year.
-   Academic year boundaries.
-   Whether students can have multiple concurrent admissions.
-   Fee types, taxes if any, scholarships, waivers, and discount
    approval limits.
-   Default partial-payment allocation policy.
-   Policy for advance payments, unallocated funds, refunds, and
    reversals.
-   Whether a payment requires approval or can be posted immediately.
-   Required personal documents and data retention period.
-   User roles and which roles can see all centres.
-   Hosting provider, object storage, email/SMS provider, and backup
    retention.
-   Export formats required at launch.
-   Whether online payment integration is needed in a later phase.

------------------------------------------------------------------------

## Instruction to the AI coding agent

Treat this document as the baseline specification. First inspect the
repository and report what exists. If the repository is empty, propose a
small implementation plan and bootstrap the project. Do not generate the
entire application in one uncontrolled pass. Implement in tested
vertical slices, ask about unresolved business rules when they affect
correctness, and keep the README and architecture documentation current.

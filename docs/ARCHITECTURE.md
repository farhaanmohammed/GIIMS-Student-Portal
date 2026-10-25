# GIIMS Architecture

## Default architecture
A modular Django monolith using PostgreSQL, Django Templates, HTMX, Alpine.js, and compiled Tailwind CSS.

## Request flow
Browser -> Django URL/view -> form validation and authorization -> service layer for business operations -> Django ORM/PostgreSQL -> template or HTMX partial response.

## Domain boundaries
- `accounts`: users, groups, roles, access policy.
- `centres`: centre master and centre membership/access.
- `academics`: academic years, courses, batches.
- `students`: durable student identity and documents.
- `admissions`: admission/enrolment events.
- `fees`: fee plans, installments, payments, allocations, adjustments, reversals.
- `receipts`: receipt representation and printing/reprinting.
- `reports`: authorized query and export workflows.
- `audit`: traceable security-sensitive and financial events.
- `core`: shared base models and small cross-cutting utilities.

Create apps as needed; do not generate empty apps solely to match a diagram. Keep views thin and put multi-model business operations in services. Use selectors/query helpers where they make complex read logic reusable.

`academics` owns the portal-wide `AcademicYear` and `Course` masters and the centre-scoped `Batch` entity, with list/create/update/toggle views under `/academics/`. Reads are open to every authenticated role, writes require `ADMIN` or `MANAGEMENT`, and batch access is filtered through `Batch.objects.for_user()`. Fee/pricing templates and seat-count enforcement deliberately live outside this app.

## Multi-centre access
Use a shared database with explicit centre relationships. Enforce centre scoping server-side in all queries and mutations. Cross-centre visibility must be explicitly granted.

## Financial consistency
Post payments and allocations atomically. Use database constraints and locking where concurrency matters. Posted payments are corrected through traceable reversals, not deletion. Outstanding values must have one documented calculation definition.

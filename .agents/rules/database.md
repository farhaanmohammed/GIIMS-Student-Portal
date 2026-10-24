---
trigger: glob
globs: "**/*.py"
description: PostgreSQL data modeling, integrity, performance, and migration rules for GIIMS.
---

# PostgreSQL and Data Modeling Best Practices

## Schema design
- Use PostgreSQL as the system of record and Django ORM by default.
- Model durable `Student` identity separately from each `Admission`/enrolment.
- Store the agreed fee terms against the admission or its fee plan so later changes to course defaults do not rewrite historical agreements.
- Use foreign keys with deliberate `on_delete` behavior and meaningful `related_name` values.
- Add database constraints for invariants that must hold even under concurrent requests.
- Add indexes based on actual query patterns; consider centre, status, date, and identifier filters, but avoid indexing every field.
- Use unique constraints for identifiers with the correct scope, such as admission numbers and receipt numbers.
- Define nullability and blank behavior deliberately; avoid nullable fields without a business reason.

## Money and balances
- Use Django `DecimalField` for monetary amounts and Python `Decimal` in calculations. Never use binary floating point for money.
- Document currency and precision. Treat INR as the intended currency only after confirming the business requirement.
- Do not keep an independently editable outstanding balance.
- Define outstanding as agreed charges plus approved debit adjustments minus posted credits/payments allocated to the admission.
- Distinguish total outstanding from overdue outstanding using due dates and a specified report “as of” date.
- Define how unallocated payments, advances, overpayments, refunds, and reversals are represented.

## Transactions and concurrency
- Use `transaction.atomic()` for workflows that must commit as a unit.
- Use `select_for_update()` when concurrent updates can cause double allocation, overpayment, or conflicting receipt issuance.
- Do not calculate sequence numbers with `COUNT(*) + 1` or unsafe `MAX(number) + 1`.
- Enforce uniqueness in the database, not only in application code.
- Make retryable financial operations idempotent using a durable, unique request/idempotency key or an equivalent design.
- Ensure a failed payment workflow rolls back all related writes.

## Migrations and operations
- Review generated migrations for lock risk, table rewrites, data-loss risk, and deployment order.
- Prefer safe, staged migrations for large tables and production data.
- Never run destructive migrations against production without explicit approval, a backup, and a rollback/recovery plan.
- Use least-privilege database credentials; do not use a PostgreSQL superuser for the web application.
- Configure automated backups, retention, monitoring, and restoration tests.
- Use synthetic data in development and tests.

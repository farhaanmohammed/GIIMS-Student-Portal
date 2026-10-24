---
trigger: glob
globs: "**/*.py"
description: Automated testing, regression, and quality-gate requirements for GIIMS.
---

# Testing Best Practices

## General
- Treat tests as part of each feature, not a final project phase.
- Use one primary test framework consistently (for example, pytest with pytest-django, or Django's built-in test runner).
- Use factories or fixtures with synthetic data only.
- Make tests deterministic and independent of production systems or external services.
- Test behavior and business outcomes, not private implementation details unnecessarily.

## Required coverage
- Model constraints, relationships, defaults, and deletion behavior.
- Forms: valid input, invalid input, missing fields, boundary values.
- Services: success, rollback, duplicate requests, and exceptions.
- Permissions: anonymous, unauthorized role, authorized role, and cross-centre access.
- Admission creation/editing and identifier uniqueness.
- Fee plans, installment schedules, partial and full payments, allocation policies, overpayments, unallocated funds, reversals, and receipt reprints.
- Outstanding and overdue calculations with different report as-of dates.
- HTMX partial and full-page response behavior.
- Report filters, pagination, exports, and export authorization.
- Upload validation and private-document access.
- Audit records for sensitive actions.

## Financial/concurrency tests
- Verify payment and allocation writes are atomic: failures must not leave partial records.
- Verify duplicate submissions cannot create duplicate collections or receipts.
- Test concurrent allocation/receipt issuance where practical using PostgreSQL-backed tests; SQLite may not reproduce PostgreSQL locking behavior.
- Assert monetary values with Decimal-compatible expectations.
- Test that posted payments cannot be silently deleted or edited.

## Quality gates
- Run focused tests after a change and the relevant full suite before completing the task.
- Run configured lint and formatting checks.
- Review migrations and verify they apply cleanly to a fresh database.
- Never claim tests passed unless they were run; report failures, skips, and unrun checks accurately.
- Include test commands and results in the completion summary.

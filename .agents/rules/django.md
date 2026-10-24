---
trigger: glob
globs: "**/*.py"
description: Django design and implementation standards for the GIIMS Student Portal.
---

# Django Best Practices

## Architecture
- Use Django as a modular monolith. Do not introduce microservices, a separate SPA, or an API framework without an explicit requirement.
- Keep responsibilities clear: models define persistence and local invariants; forms validate user input; views coordinate requests and responses; services implement multi-model business workflows; templates render UI.
- Keep views thin. Put complex workflows in explicit, testable service functions.
- Add selector/query helpers when complex read queries are reused; do not create abstractions that only wrap a single ORM call without value.
- Keep business logic out of templates, HTMX attributes, and Alpine.js.

## Models and migrations
- If using a custom user model, define `AUTH_USER_MODEL` before the first migration.
- Use explicit field types, related names, `on_delete` behavior, model constraints, and indexes.
- Use `TextChoices`/`IntegerChoices` or equivalent explicit choices instead of magic strings.
- Do not store derived values redundantly unless there is a documented performance need and a reconciliation strategy.
- Review every generated migration. Avoid unreviewed destructive changes and data migrations that assume all rows are valid.
- Do not casually cascade-delete students, admissions, payments, receipts, or audit records.

## Requests and validation
- Use Django Forms or ModelForms for server-side validation and clear field-level errors.
- Treat all browser input as untrusted. Re-fetch and authorize submitted object IDs server-side.
- Use POST for state-changing operations and GET for safe reads only.
- Keep CSRF protection enabled for normal and HTMX requests.
- Return correct status codes and helpful, non-sensitive error messages.
- Do not expose exception traces or internal implementation details to users.

## ORM and performance
- Use `select_related()` and `prefetch_related()` to avoid N+1 queries.
- Paginate large tables and validate/sanitize filter and ordering parameters.
- Use database aggregation for large reports where appropriate.
- Avoid raw SQL unless necessary; parameterize all raw SQL values.
- Do not perform slow work in request handlers if it can safely be moved to a background job later.

## Configuration and code quality
- Read configuration from environment variables; keep secrets out of source control.
- Separate local and production settings or use an equally clear environment-specific configuration strategy.
- Use timezone-aware datetimes and document the configured timezone.
- Prefer clear names, small functions, type hints for non-trivial service code, and consistent formatting.
- Keep dependencies minimal and verify compatibility with the selected Python/Django versions before adding packages.
- Add tests for behavior changes and update documentation when setup or behavior changes.

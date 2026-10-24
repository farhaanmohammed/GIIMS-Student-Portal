# GIIMS Development Workflow

1. Read `AGENTS.md`, `README.md`, and `docs/PROJECT_SPEC.md`.
2. Confirm unresolved business rules before implementing affected financial behavior.
3. Implement one vertical slice at a time: model/migration, service, form/view, template, permissions, tests.
4. Review migrations and database constraints.
5. Run relevant tests and lint/format checks; record exact results.
6. Verify centre scoping and access permissions.
7. Update docs and README when behavior or setup changes.
8. Use synthetic data only.
9. Never perform destructive data or production operations without explicit approval.

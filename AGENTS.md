# AGENTS.md --- Instructions for AI Coding Agents

You are contributing to the GIIMS Student Portal. Follow
`docs/PROJECT_SPEC.md` as the product and engineering source of truth
and `README.md` for setup.

## Operating rules

1.  Inspect the existing repository before editing. Do not overwrite
    existing work without understanding it.
2.  Work in small, coherent, tested increments. Do not attempt to
    generate the entire application in one pass.
3.  Use Django as a modular monolith, PostgreSQL for persistence, Django
    templates for rendering, HTMX for server-driven partial updates, and
    Alpine.js for lightweight client-side interactions.
4.  Keep business rules in tested Python services/domain functions, not
    in templates or JavaScript.
5.  Treat authorization and centre-level scoping as server-side
    requirements for every read, write, report, and export.
6.  Financial writes must use explicit services and appropriate database
    transactions/locking. Never trust client-submitted totals or receipt
    numbers.
7.  Never hard-delete posted payments. Use traceable reversal/correction
    workflows.
8.  Use `DecimalField` for money and timezone-aware datetimes.
9.  Add or update tests with every meaningful behavior change. Do not
    claim tests passed unless they were run.
10. Review migrations, constraints, indexes, and `on_delete` choices
    before considering a model change complete.
11. Use synthetic data only. Never commit credentials, `.env` files,
    production data, or private student documents.
12. Keep dependencies minimal, compatible, and pinned. Verify current
    compatibility before adding a package.
13. Use semantic, accessible HTML and preserve CSRF protection on all
    state-changing requests, including HTMX requests.
14. Update README/docs when setup, behavior, configuration, or
    architecture changes.
15. Do not add unrelated frameworks, microservices, a SPA, or an API
    layer without a clear requirement.
16. Ask for clarification instead of inventing financial policy, receipt
    numbering, admission ID formats, discount rules, or refund behavior.
17. Before destructive actions or changes to production systems/data,
    stop and ask for explicit approval.

## Suggested implementation loop

1.  State the intended vertical slice and affected files.
2.  Implement model/schema and migration.
3.  Implement service/business logic.
4.  Implement forms, views, URLs, templates, and HTMX behavior.
5.  Add permissions and centre scoping.
6.  Add tests for success, validation failures, permissions, and edge
    cases.
7.  Run relevant checks and report the exact results.
8.  Summarize files changed, tests run, decisions needed, and follow-up
    work.

## Completion checklist

-   [ ] Business rules enforced on the server
-   [ ] Authorization and centre scoping verified
-   [ ] Database constraints and transaction boundaries reviewed
-   [ ] Tests added and run
-   [ ] Lint/format checks run where configured
-   [ ] No secrets or real personal data added
-   [ ] UI validation, empty, loading, and error states considered
-   [ ] Documentation updated

---
trigger: always_on
description: Mandatory security and privacy rules for all GIIMS project changes.
---

# Security and Privacy Best Practices

## Access control
- Deny access by default. Enforce authentication, role permissions, and centre-level data scope on the server for every page, HTMX endpoint, mutation, receipt download, and export.
- Hiding a button is not authorization.
- Re-check ownership/centre access for every object ID submitted by the client to prevent IDOR/BOLA vulnerabilities.
- Restrict cross-centre visibility to explicitly authorized roles.
- Keep privileged actions such as discounts, refunds, reversals, and fee-plan changes permission-controlled and auditable.

## Web security
- Use Django's built-in authentication, password hashing, sessions, permissions, and CSRF middleware.
- Do not disable CSRF protection for HTMX or AJAX requests.
- Escape untrusted output. Avoid `mark_safe`, unsafe HTML insertion, and raw user-provided HTML unless there is a reviewed sanitization design.
- Use POST/appropriate methods for state changes and GET for safe reads.
- Validate input server-side; use ORM parameterization and never build SQL by interpolating user input.
- Do not expose stack traces, secrets, tokens, or sensitive details in error messages.
- Apply rate limiting or abuse protections to sensitive endpoints when appropriate.

## Secrets and environments
- Never commit `.env`, credentials, API keys, tokens, private keys, production database dumps, or student records.
- Keep `.env.example` limited to dummy placeholders.
- Production must have `DEBUG=False`, secure secret management, HTTPS, appropriate allowed hosts, secure cookies, trusted origins, and correctly configured proxy settings.
- Use least-privilege application/database credentials.
- Keep dependencies patched and review security advisories.

## Uploads and personal data
- Treat student and guardian details, identity documents, and financial records as sensitive.
- Validate upload size, extension, and content where feasible; never trust browser MIME types.
- Store private documents outside public static directories and serve them only after authorization.
- Collect only necessary data and define retention/deletion policies with the business owner.
- Use synthetic records in development, demos, tests, and screenshots.
- Avoid logging passwords, tokens, full identity numbers, or unnecessary personal/financial data.

## Audit, backups, and incident readiness
- Record actor, action, target, timestamp, and safe change summary for sensitive operations.
- Protect logs and backups from unauthorized access.
- Encrypt backups where appropriate and test restoration.
- Ask before destructive operations or production changes.

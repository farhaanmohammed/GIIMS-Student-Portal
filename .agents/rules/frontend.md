---
trigger: glob
globs: "templates/**/*.html"
description: Django Templates, HTMX, Alpine.js, and Tailwind CSS UI standards for GIIMS.
---

# Frontend Best Practices

## Responsibilities
- Use Django Templates for server-rendered pages and HTML fragments.
- Use HTMX for server-driven search, filtering, pagination, dependent form fields, and partial refreshes.
- Use Alpine.js for small local interactions such as dropdowns, tabs, modal visibility, and toggles.
- Keep business rules, authorization, fee calculations, and validation on the server.
- Do not add React, Vue, or a separate SPA without a clear approved requirement.

## HTMX
- Keep full-page views and HTMX partial responses consistent in behavior and permissions.
- Use predictable template partials and clear endpoint names.
- Return useful validation errors and appropriate HTTP status codes.
- Include loading indicators and disable repeated submit controls when appropriate, while still enforcing idempotency server-side.
- Use `hx-confirm` only as a user-experience safeguard; it is not a security control.
- Use out-of-band swaps sparingly and test them.
- Do not mutate state through GET requests.
- Ensure redirects and response behavior are suitable for HTMX requests.

## Alpine.js
- Use Alpine for presentation state only; never trust it for authorization, pricing, balances, or validation.
- Do not put secrets or sensitive personal/financial data in client-side state unnecessarily.
- Keep components small and avoid duplicating server-rendered data into JavaScript without need.
- Ensure interactive controls remain keyboard accessible.

## Forms and accessibility
- Use semantic HTML and explicit labels linked to controls.
- Show field-level errors and a clear summary for form errors.
- Support keyboard navigation, visible focus, accessible names, and adequate contrast.
- Do not rely on color alone to convey status.
- Confirm destructive or financially sensitive actions and explain the consequence.
- Provide empty, loading, success, error, and disabled states as appropriate.
- Use responsive layouts, especially for admission forms and wide reports.
- Preserve active filters during pagination and exports.

## Assets and performance
- Compile Tailwind CSS for production and pin frontend dependencies.
- Avoid unpinned runtime CDNs for core dependencies.
- Use Django's static-file pipeline correctly and keep user uploads separate from static assets.
- Keep JavaScript minimal and test important interactions.

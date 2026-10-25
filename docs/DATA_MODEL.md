# GIIMS Logical Data Model

This document mixes the implemented schema with entities that are still logical. Confirm the open business decisions in `PROJECT_SPEC.md` before finalizing field names and constraints for planned entities.

## Implemented — Phase 1 foundations

- **User**: custom Django user model (`AbstractUser`) with `role`, `primary_centre`, a `centres` many-to-many access scope, and a `can_access_all_centres` flag.
- **Centre**: centre code, name, address, email, phone, and active status.
- **CentreMembership**: not modelled. Centre access is held directly on `User` (`primary_centre` plus `centres`).

## Implemented — Phase 2 Academic Masters

### AcademicYear (portal-wide, `apps.academics`)
- `label`: unique free text, max 20 characters, stripped on save. No separator or year-format convention is enforced.
- `start_date`, `end_date`: required dates; check constraint `academic_year_end_after_start` requires `end_date > start_date`.
- `is_active`: boolean, default `False`, indexed. Marks the year as open for new enrolments; more than one year may be active because no constraint prevents it.
- `is_current`: derived property, true when today falls within the date range. Nothing is stored.
- `created_at`, `updated_at` from `TimeStampedModel`.

### Course (portal-wide, `apps.academics`)
- `code`: unique, max 20 characters, validated against `^[A-Z0-9][A-Z0-9-]*$`; normalized to uppercase in `save()` and `clean_fields()`.
- `name`: max 150 characters.
- `duration_months`: positive small integer, 1–120, default 12.
- `description`: optional text.
- `is_active`: boolean, default `True`, indexed.
- No fee, pricing, or discount fields. Fee templates are deliberately deferred to Phase 4.

### Batch (centre-scoped, `apps.academics`)
- `centre`: foreign key to `centres.Centre`, `on_delete=PROTECT`, indexed; inherited from `CentreScopedModel`, so `Batch.objects.for_user(user)` filters rows by accessible centre.
- `course`: foreign key to `Course`, `on_delete=PROTECT`.
- `academic_year`: foreign key to `AcademicYear`, `on_delete=PROTECT`.
- `code`: max 30 characters, normalized to uppercase; unique per centre, course, and academic year via `batch_unique_code_per_centre_course_year`. The code format itself is free text.
- `start_date`, `end_date`: required dates; check constraint `batch_end_after_start`. Batch dates are not constrained to fall inside the academic year.
- `capacity`: positive integer, nullable with no default. `NULL` means the centre has not specified a capacity; check constraint `batch_capacity_positive` allows null or at least 1.
- `is_active`: boolean, default `True`, indexed.
- No derived seat counters or enrolment totals are stored. Capacity enforcement belongs to Phase 3 admissions.

### Scoping summary
- `AcademicYear` and `Course` are portal-wide master data: every authenticated role can read them, and writes are restricted to `ADMIN` and `MANAGEMENT`.
- `Batch` is the first centre-scoped domain entity: reads and writes go through `Batch.objects.for_user()`, and centre choices in the batch form come from `User.get_accessible_centres()`.

## Planned / logical entities (Phases 3–6)

- **Student**: durable personal identity and contact information.
- **Admission**: student, course, batch, centre, academic year, admission date, status, admission number.
- **FeePlan**: admission, agreed charge details, discounts/approved adjustments, currency.
- **FeeInstallment**: fee plan, due date, amount, description, status.
- **Payment**: admission, amount, method, paid/posted date, reference, status, actor, idempotency key.
- **PaymentAllocation**: payment, installment, allocated amount.
- **FeeAdjustment**: admission/fee plan, type, amount, reason, approval metadata.
- **PaymentReversal**: payment, reason, actor, timestamp, reversal metadata.
- **Receipt**: payment, receipt number, issue timestamp, status/void metadata.
- **StudentDocument**: student/admission, document type, private storage key, metadata.
- **AuditEvent**: actor, event type, target reference, timestamp, safe change summary.

## Key rules
- A student can potentially have more than one admission; model this explicitly.
- Preserve the fee agreement attached to an admission even if course defaults later change.
- Use DecimalField for money.
- Avoid a manually editable outstanding balance.
- Use deliberate on-delete policies and database constraints.
- Define whether payment allocation is oldest-due-first or user-selected.
- Define receipt number uniqueness scope before implementation.
- Do not store derived seat counts on `Batch`; calculate availability when admissions are implemented.
- Keep fee/pricing templates off the academic masters until fee types and discount rules are confirmed.

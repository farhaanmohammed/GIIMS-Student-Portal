# GIIMS Logical Data Model

This is a starting logical model. Confirm the open business decisions in `PROJECT_SPEC.md` before finalizing field names and constraints.

- **User**: custom Django user model; groups/permissions.
- **Centre**: centre code, name, address, status.
- **CentreMembership** (if needed): user, centre, role/access scope.
- **AcademicYear**: label, start/end dates, active state.
- **Course**: code, name, duration, default pricing reference, status.
- **Batch**: course, centre, academic year, dates, status.
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

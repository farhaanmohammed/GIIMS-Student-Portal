from django.db import IntegrityError, transaction

from apps.students.models import Student, StudentIdSequence

ID_SEQUENCE_PADDING = 3


def build_student_id_prefix(admission) -> str:
    """
    Return the ID prefix for an admission: ``{centre}-{batch}-{course}``.

    Codes are captured at enrollment time; changing a master record later
    never rewrites an already issued student ID.
    """
    return "-".join(
        (
            admission.centre.code,
            admission.batch.code,
            admission.course.code,
        )
    )


def format_student_id(prefix: str, number: int) -> str:
    """Render a student ID, zero-padded to at least three digits."""
    return f"{prefix}_{number:0{ID_SEQUENCE_PADDING}d}"


def _next_sequence_number(prefix: str) -> int:
    """
    Reserve and return the next number for ``prefix``.

    Must run inside a transaction. The sequence row is locked so concurrent
    enrollments serialize instead of allocating the same number.
    """
    try:
        with transaction.atomic():
            sequence, _ = StudentIdSequence.objects.select_for_update().get_or_create(prefix=prefix)
    except IntegrityError:
        # Another transaction created the row first; retry against the
        # now-existing sequence.
        sequence = StudentIdSequence.objects.select_for_update().get(prefix=prefix)

    sequence.last_number += 1
    sequence.save(update_fields=["last_number", "updated_at"])
    return sequence.last_number


def generate_student_id_for_enrollment(admission) -> str:
    """
    Assign and return the permanent student ID for an admission.

    Format: ``{centre.code}-{batch.code}-{course.code}_{NNN}`` (for example
    ``TVM-ASP-AD_001``). Called once when the admission becomes ``ENROLLED``.
    Idempotent: an already assigned ID is returned unchanged.
    """
    with transaction.atomic():
        # ``admission.student_id`` is the FK value (student PK); the readable
        # identifier lives on ``student.student_id``.
        student = Student.objects.select_for_update().get(pk=admission.student_id)
        if student.student_id:
            return student.student_id

        prefix = build_student_id_prefix(admission)
        number = _next_sequence_number(prefix)
        while Student.objects.filter(student_id=format_student_id(prefix, number)).exists():
            # Defensive: never reuse an ID that already exists.
            number = _next_sequence_number(prefix)

        student.student_id = format_student_id(prefix, number)
        student.save(update_fields=["student_id", "updated_at"])
        return student.student_id

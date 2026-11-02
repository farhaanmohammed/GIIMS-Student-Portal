from django.db import transaction

from apps.students.models import Student


def generate_student_id_for_enrollment(admission):
    """
    Generate and assign student_id on enrollment.
    Format: {Centre.code}-{Batch.code}-{Course.code}_{NNN}
    Sequence resets per academic year context, scoped by prefix.
    Always 3 digits, zero-padded. Never changes once assigned.
    """
    student = admission.student
    if student.student_id:
        return student.student_id

    centre_code = admission.centre.code
    batch_code = admission.batch.code
    course_code = admission.course.code
    academic_year = admission.academic_year

    prefix = f"{centre_code}-{batch_code}-{course_code}_"
    prefix_full = f"{centre_code}-{batch_code}-{course_code}"

    with transaction.atomic():
        # Lock to prevent race conditions
        student = Student.objects.select_for_update().get(pk=student.pk)

        if student.student_id:
            return student.student_id

        # Find max existing number for this exact prefix in context
        # Sequence resets every year; batch changes each year so naturally unique
        # But also filter by academic year of admissions to be explicit
        existing = (
            Student.objects.filter(
                student_id__startswith=prefix_full,
                admissions__academic_year=academic_year,
            )
            .select_for_update()
            .values_list("student_id", flat=True)
        )

        max_num = 0
        for sid in existing:
            if sid and sid.startswith(prefix):
                try:
                    num_part = sid.split("_")[-1]
                    num = int(num_part)
                    if num > max_num:
                        max_num = num
                except (ValueError, IndexError):
                    continue

        next_num = max_num + 1
        student_id = f"{prefix}{next_num:03d}"
        student.student_id = student_id
        student.save(update_fields=["student_id"])
        return student_id

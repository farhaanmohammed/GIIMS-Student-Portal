from django.db import transaction
from django.utils import timezone

from apps.admissions.models import Admission
from apps.students.services import generate_student_id_for_enrollment


def enroll_admission(admission, user):
    """
    Transition admission from ENQUIRED to ENROLLED.
    Assigns student_id on enrollment if not already assigned.
    """
    if admission.status != Admission.Status.ENQUIRED:
        raise ValueError("Only ENQUIRED admissions can be enrolled.")

    with transaction.atomic():
        admission = Admission.objects.select_for_update().get(pk=admission.pk)
        admission.status = Admission.Status.ENROLLED
        admission.enrolled_at = timezone.now()
        admission.enrolled_by = user
        admission.save(update_fields=["status", "enrolled_at", "enrolled_by", "updated_at"])

        generate_student_id_for_enrollment(admission)
        return admission

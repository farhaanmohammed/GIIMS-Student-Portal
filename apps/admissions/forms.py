from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError

from apps.admissions.models import Admission
from apps.students.models import Student

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-slate-900 "
    "shadow-xs focus:border-indigo-600 focus:outline-none focus:ring-1 "
    "focus:ring-indigo-600 sm:text-sm"
)


def _date_input() -> forms.DateInput:
    """Return a fresh date widget so fields never share a mutable widget."""
    return forms.DateInput(attrs={"class": INPUT_CLASSES, "type": "date"})


def _text_input() -> forms.TextInput:
    return forms.TextInput(attrs={"class": INPUT_CLASSES})


class StudentEnquiryForm(forms.ModelForm):
    """Collect the durable student identity details during an enquiry."""

    class Meta:
        model = Student
        fields = (
            "first_name",
            "last_name",
            "middle_name",
            "date_of_birth",
            "gender",
            "phone",
            "email",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "pincode",
            "guardian_name",
            "guardian_phone",
            "guardian_relation",
        )
        widgets: ClassVar[dict[str, Any]] = {
            "first_name": _text_input(),
            "last_name": _text_input(),
            "middle_name": _text_input(),
            "date_of_birth": _date_input(),
            "gender": forms.Select(attrs={"class": INPUT_CLASSES}),
            "phone": _text_input(),
            "email": forms.EmailInput(attrs={"class": INPUT_CLASSES}),
            "address_line1": _text_input(),
            "address_line2": _text_input(),
            "city": _text_input(),
            "state": _text_input(),
            "pincode": _text_input(),
            "guardian_name": _text_input(),
            "guardian_phone": _text_input(),
            "guardian_relation": _text_input(),
        }

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            raise ValidationError("Phone number is required.")
        return phone

    def clean_guardian_phone(self):
        phone = self.cleaned_data.get("guardian_phone", "").strip()
        if not phone:
            raise ValidationError("Guardian phone number is required.")
        return phone


class AdmissionEnquiryForm(forms.ModelForm):
    """Collect the admission placement details for an enquiry."""

    class Meta:
        model = Admission
        fields = ("centre", "course", "batch", "academic_year")
        widgets: ClassVar[dict[str, Any]] = {
            "centre": forms.Select(attrs={"class": INPUT_CLASSES}),
            "course": forms.Select(attrs={"class": INPUT_CLASSES}),
            "batch": forms.Select(attrs={"class": INPUT_CLASSES}),
            "academic_year": forms.Select(attrs={"class": INPUT_CLASSES}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user
        if user is not None:
            self.fields["centre"].queryset = user.get_accessible_centres()
            if user.primary_centre_id:
                self.fields["centre"].initial = user.primary_centre

    def clean_centre(self):
        centre = self.cleaned_data.get("centre")
        if self._user is not None and centre and not self._user.can_access_centre(centre):
            raise ValidationError("You do not have access to this centre.")
        return centre

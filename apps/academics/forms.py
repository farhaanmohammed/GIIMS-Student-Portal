from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError

from apps.academics.models import COURSE_CODE_VALIDATOR, AcademicYear, Batch, Course
from apps.centres.models import Centre

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-slate-900 "
    "shadow-xs focus:border-indigo-600 focus:outline-none focus:ring-1 "
    "focus:ring-indigo-600 sm:text-sm"
)
CHECKBOX_CLASSES = (
    "h-4 w-4 rounded border-slate-300 text-indigo-600 "
    "focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2"
)


def _date_input() -> forms.DateInput:
    """Return a fresh date widget so fields never share a mutable widget."""
    return forms.DateInput(attrs={"class": INPUT_CLASSES, "type": "date"})


class AcademicYearForm(forms.ModelForm):
    """Create or update a portal-wide academic year."""

    class Meta:
        model = AcademicYear
        fields = ("label", "start_date", "end_date", "is_active")
        widgets: ClassVar[dict[str, Any]] = {
            "label": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. 2026-2027",
                    "autocomplete": "off",
                }
            ),
            "start_date": _date_input(),
            "end_date": _date_input(),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        labels: ClassVar[dict[str, str]] = {
            "label": "Academic year label",
            "start_date": "Start date",
            "end_date": "End date",
            "is_active": "Active for new enrolments",
        }

    def clean_label(self) -> str:
        label = (self.cleaned_data.get("label") or "").strip()
        if not label:
            raise forms.ValidationError("Enter an academic year label.")
        return label

    def clean(self) -> dict:
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after the start date.")
        return cleaned_data


class CourseForm(forms.ModelForm):
    """
    Create or update a portal-wide course.

    ``code`` is redeclared as a plain character field so that user input is
    normalized (stripped and upper-cased) before the model's uppercase-only
    regex validator runs.
    """

    code = forms.CharField(
        max_length=20,
        label="Course code",
        help_text="Uppercase letters, digits, and hyphens only (e.g. AVM-01).",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": "e.g. AVM-01",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Course
        fields = ("code", "name", "duration_months", "description", "is_active")
        widgets: ClassVar[dict[str, Any]] = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. Aviation Management",
                    "autocomplete": "off",
                }
            ),
            "duration_months": forms.NumberInput(
                attrs={"class": INPUT_CLASSES, "min": 1, "max": 120}
            ),
            "description": forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        labels: ClassVar[dict[str, str]] = {
            "name": "Course name",
            "duration_months": "Duration (months)",
            "description": "Description",
            "is_active": "Active for new batches and admissions",
        }

    def clean_code(self) -> str:
        value = (self.cleaned_data.get("code") or "").strip().upper()
        try:
            COURSE_CODE_VALIDATOR(value)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return value


class BatchForm(forms.ModelForm):
    """
    Create or update a batch.

    The centre choices are always limited to the centres the requesting user
    may access, and the submitted centre is re-checked server-side.
    """

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        centre_field = self.fields["centre"]
        centre_field.queryset = (
            user.get_accessible_centres() if user is not None else Centre.objects.none()
        )

        # Preselect the only accessible centre for single-centre users.
        if not self.is_bound and self.instance._state.adding:
            accessible = list(centre_field.queryset[:2])
            if len(accessible) == 1:
                self.initial.setdefault("centre", accessible[0].pk)

    class Meta:
        model = Batch
        fields = (
            "centre",
            "course",
            "academic_year",
            "code",
            "start_date",
            "end_date",
            "capacity",
            "is_active",
        )
        widgets: ClassVar[dict[str, Any]] = {
            "centre": forms.Select(attrs={"class": INPUT_CLASSES}),
            "course": forms.Select(attrs={"class": INPUT_CLASSES}),
            "academic_year": forms.Select(attrs={"class": INPUT_CLASSES}),
            "code": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": "e.g. 2026-A",
                    "autocomplete": "off",
                }
            ),
            "start_date": _date_input(),
            "end_date": _date_input(),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASSES, "min": 1}),
            "is_active": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
        }
        labels: ClassVar[dict[str, str]] = {
            "centre": "Centre",
            "course": "Course",
            "academic_year": "Academic year",
            "code": "Batch code",
            "start_date": "Start date",
            "end_date": "End date",
            "capacity": "Capacity (optional)",
            "is_active": "Accepting new admissions",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "code": "Batch identifier within the centre, course, and academic year.",
            "capacity": "Leave blank when the centre has not set a capacity.",
        }

    def clean_code(self) -> str:
        value = (self.cleaned_data.get("code") or "").strip().upper()
        if not value:
            raise forms.ValidationError("Enter a batch code.")
        return value

    def clean_centre(self) -> Centre | None:
        centre = self.cleaned_data.get("centre")
        if centre is not None and not (
            self.user is not None and self.user.can_access_centre(centre)
        ):
            raise forms.ValidationError("You do not have access to the selected centre.")
        return centre

    def clean_capacity(self) -> int | None:
        capacity = self.cleaned_data.get("capacity")
        if capacity is not None and capacity < 1:
            raise forms.ValidationError("Capacity must be at least 1, or leave it blank.")
        return capacity

    def clean(self) -> dict:
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after the start date.")

        centre = cleaned_data.get("centre")
        course = cleaned_data.get("course")
        academic_year = cleaned_data.get("academic_year")
        code = cleaned_data.get("code")
        if centre and course and academic_year and code:
            duplicates = Batch.objects.filter(
                centre=centre,
                course=course,
                academic_year=academic_year,
                code=code,
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error(
                    "code",
                    "A batch with this code already exists for the selected "
                    "centre, course, and academic year.",
                )
        return cleaned_data

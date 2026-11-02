from django import forms
from django.core.exceptions import ValidationError

from apps.admissions.models import Admission
from apps.students.models import Student


class StudentEnquiryForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = [
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
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date", "class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "first_name": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "last_name": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "middle_name": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "gender": forms.Select(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "phone": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "email": forms.EmailInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "address_line1": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "address_line2": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "city": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "state": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "pincode": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "guardian_name": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "guardian_phone": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "guardian_relation": forms.TextInput(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
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
    class Meta:
        model = Admission
        fields = ["centre", "course", "batch", "academic_year"]
        widgets = {
            "centre": forms.Select(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "course": forms.Select(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "batch": forms.Select(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
            "academic_year": forms.Select(attrs={"class": "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-indigo-500 sm:text-sm"}),
        }

    def __init__(self, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            from django.db.models import Q

            accessible = user.get_accessible_centres()
            self.fields["centre"].queryset = accessible
            if accessible.exists():
                self.fields["centre"].initial = user.primary_centre

    def clean_centre(self):
        centre = self.cleaned_data.get("centre")
        user = getattr(self, "_user", None)
        if user and centre and not user.can_access_centre(centre):
            raise ValidationError("You do not have access to this centre.")
        return centre

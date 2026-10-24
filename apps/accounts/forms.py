from django import forms
from django.contrib.auth.forms import AuthenticationForm

INPUT_CLASSES = (
    "block w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-slate-900 "
    "shadow-xs focus:border-indigo-600 focus:outline-none focus:ring-1 "
    "focus:ring-indigo-600 sm:text-sm"
)


class GIIMSAuthenticationForm(AuthenticationForm):
    """
    Standard login authentication form styled for GIIMS education portal.
    """

    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": "Enter institutional username",
                "autofocus": True,
            }
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": "Enter password",
            }
        )
    )

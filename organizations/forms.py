from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from core.forms import BootstrapFormMixin

from .models import DEFAULT_TIMEZONE, Organization, OrganizationMembership, timezone_choices

User = get_user_model()


class BusinessProfileForm(BootstrapFormMixin, forms.ModelForm):
    timezone = forms.ChoiceField(choices=timezone_choices, initial=DEFAULT_TIMEZONE)

    class Meta:
        model = Organization
        fields = [
            "name", "description", "phone", "whatsapp_number",
            "email", "address", "timezone", "logo",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "What does your business sell or do?"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "phone": forms.TextInput(attrs={"placeholder": "+263 77 123 4567"}),
            "whatsapp_number": forms.TextInput(attrs={"placeholder": "+263 77 123 4567"}),
            "email": forms.EmailInput(attrs={"placeholder": "hello@yourbusiness.com"}),
            "logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif", "data-logo-input": ""}),
        }
        labels = {"name": "Business name", "whatsapp_number": "WhatsApp number", "email": "Business email"}
        help_texts = {"whatsapp_number": "Customers will be routed here in a future release."}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter your business name.")
        return name

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and hasattr(logo, "size") and logo.size > settings.LOGO_MAX_UPLOAD_BYTES:
            raise forms.ValidationError("Logo must be 2 MB or smaller.")
        return logo


class CreateOrganizationForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(
        label="Business name", max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Sunrise Hardware", "autofocus": True}),
    )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter your business name.")
        return name


class ChangeRoleForm(forms.Form):
    role = forms.ChoiceField(choices=OrganizationMembership.Role.choices)

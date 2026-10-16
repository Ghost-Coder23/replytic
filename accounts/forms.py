from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm,
)
from django.contrib.auth.password_validation import validate_password

from core.forms import BootstrapFormMixin

from . import services

User = get_user_model()


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Email",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "placeholder": "you@business.com"}),
    )
    password = forms.CharField(
        label="Password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Your password"}),
    )
    remember_me = forms.BooleanField(required=False, initial=True, label="Keep me signed in")
    error_messages = {
        "invalid_login": "That email and password don't match. Check them and try again.",
        "inactive": "This account is inactive. Contact your business owner.",
    }


class RegistrationForm(BootstrapFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "given-name", "autofocus": True}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    email = forms.EmailField(max_length=150, widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@business.com"}))
    business_name = forms.CharField(
        label="Business name", max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Brandon Furniture", "autocomplete": "organization"}),
    )
    password1 = forms.CharField(
        label="Password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 8 characters. Avoid common passwords.",
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if services.email_in_use(email):
            raise forms.ValidationError("An account with this email already exists. Try signing in instead.")
        return email

    def clean_business_name(self):
        name = self.cleaned_data["business_name"].strip()
        if not name:
            raise forms.ValidationError("Enter your business name.")
        return name

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords don't match.")
        elif password1:
            candidate = User(
                username=cleaned.get("email", ""), email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""), last_name=cleaned.get("last_name", ""),
            )
            try:
                validate_password(password1, candidate)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return cleaned

    def save(self):
        return services.register_user_with_organization(
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
            business_name=self.cleaned_data["business_name"],
        )


class AccountProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["first_name"].required = True
        self.fields["email"].max_length = 150

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if services.email_in_use(email, exclude_user=self.instance):
            raise forms.ValidationError("Another account already uses this email.")
        return email

    def save(self, commit=True):
        return services.update_account(
            user=self.instance,
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            email=self.cleaned_data["email"],
            previous_email=self.initial.get("email", ""),
        )


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class StyledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    email = forms.EmailField(max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True, "placeholder": "you@business.com"}))


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass

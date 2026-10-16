from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import (
    LoginView, PasswordChangeView, PasswordResetCompleteView, PasswordResetConfirmView,
    PasswordResetDoneView, PasswordResetView,
)
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from organizations.services import SESSION_KEY

from .forms import (
    LoginForm, RegistrationForm, StyledPasswordChangeForm, StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .services import EmailAlreadyRegistered


class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # expire when the browser closes
        return response


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("dashboard:index")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:index")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            user, organization, _ = form.save()
        except EmailAlreadyRegistered:
            form.add_error("email", "An account with this email already exists. Try signing in instead.")
            return self.form_invalid(form)
        login(self.request, user)
        self.request.session[SESSION_KEY] = str(organization.pk)
        messages.success(self.request, f"Your account has been created. Welcome to Replytic, {user.first_name}!")
        return super().form_valid(form)


class SignOutView(View):
    """POST signs out. GET shows a confirmation so a stray link can't log you out."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        return TemplateView.as_view(template_name="accounts/logout.html")(request)

    def post(self, request):
        if request.user.is_authenticated:
            logout(request)
            messages.success(request, "You've been signed out.")
        return redirect("accounts:login")


class ChangePasswordView(LoginRequiredMixin, PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("organizations:settings")

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed.")
        return super().form_valid(form)

    def get_success_url(self):
        return f"{reverse_lazy('organizations:settings')}?tab=security"


class ResetPasswordView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")


class ResetPasswordDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class ResetPasswordConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class ResetPasswordCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"

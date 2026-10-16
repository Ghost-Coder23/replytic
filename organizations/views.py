from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts.forms import AccountProfileForm
from core.mixins import OrganizationRequiredMixin
from django.contrib.auth.mixins import LoginRequiredMixin

from . import services
from .forms import BusinessProfileForm, ChangeRoleForm, CreateOrganizationForm
from .models import OrganizationMembership
from .permissions import can_change_member_role, can_remove_member, has_permission


class CreateOrganizationView(LoginRequiredMixin, FormView):
    """Create an additional business; the creator becomes its OWNER."""

    template_name = "organizations/create.html"
    form_class = CreateOrganizationForm

    def form_valid(self, form):
        organization, _ = services.create_organization(owner=self.request.user, name=form.cleaned_data["name"])
        services.switch_organization(
            user=self.request.user, session=self.request.session, organization_id=organization.pk
        )
        messages.success(self.request, f"{organization.name} is ready. You're now working in it.")
        return redirect("dashboard:index")


class SwitchOrganizationView(LoginRequiredMixin, View):
    """POST-only. The submitted id is validated against the user's memberships."""

    http_method_names = ["post"]

    def post(self, request):
        membership = services.switch_organization(
            user=request.user, session=request.session,
            organization_id=request.POST.get("organization_id", ""),
        )
        if membership is None:
            messages.error(request, "You don't have access to that business.")
        else:
            messages.success(request, f"Switched to {membership.organization.name}.")
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("dashboard:index")


class TeamView(OrganizationRequiredMixin, TemplateView):
    template_name = "organizations/team.html"
    required_permission = "view_team"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Tenant isolation: members are always filtered by the current organization.
        members = list(
            OrganizationMembership.objects.filter(organization=self.organization)
            .select_related("user")
            .order_by("created_at")
        )
        for member in members:
            member.can_change_role = can_change_member_role(self.membership, member)
            member.can_remove = can_remove_member(self.membership, member)
            member.is_you = member.user_id == self.request.user.pk
        context.update({
            "members": members,
            "role_choices": OrganizationMembership.Role.choices,
            "can_manage_anyone": any(m.can_change_role or m.can_remove for m in members),
        })
        return context


class _MemberActionView(OrganizationRequiredMixin, View):
    """Base for POST actions on a single membership of the *current* organization."""

    http_method_names = ["post"]
    required_permission = "manage_team"

    def get_member(self, membership_id):
        # A membership id from another organization simply does not exist here (404).
        return get_object_or_404(
            OrganizationMembership.objects.select_related("user"),
            pk=membership_id, organization=self.organization,
        )


class ChangeMemberRoleView(_MemberActionView):
    required_permission = "change_member_roles"

    def post(self, request, membership_id):
        target = self.get_member(membership_id)
        if not can_change_member_role(self.membership, target):
            raise PermissionDenied
        form = ChangeRoleForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose a valid role.")
            return redirect("organizations:team")
        try:
            services.change_member_role(membership=target, new_role=form.cleaned_data["role"])
        except services.MembershipError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"{target.user.get_full_name() or target.user.email} is now {target.get_role_display()}.")
        return redirect("organizations:team")


class RemoveMemberView(_MemberActionView):
    def post(self, request, membership_id):
        target = self.get_member(membership_id)
        if not can_remove_member(self.membership, target):
            raise PermissionDenied
        name = target.user.get_full_name() or target.user.email
        try:
            services.remove_member(membership=target)
        except services.MembershipError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"{name} was removed from {self.organization.name}.")
        return redirect("organizations:team")


SETTINGS_TABS = [
    # key, label, icon, permission needed to see the tab
    ("business", "Business profile", "bi-shop", "manage_business_profile"),
    ("account", "Account", "bi-person", "view_dashboard"),
    ("security", "Security", "bi-shield-lock", "view_dashboard"),
    ("team", "Team", "bi-people", "view_team"),
    ("integrations", "Integrations", "bi-plug", "manage_integrations"),
    ("billing", "Billing", "bi-credit-card", "view_billing"),
]


class SettingsView(OrganizationRequiredMixin, View):
    """Tabbed settings. Business tab edits the *current* organization only."""

    template_name = "organizations/settings.html"
    required_permission = "view_dashboard"

    def available_tabs(self):
        return [
            {"key": key, "label": label, "icon": icon}
            for key, label, icon, permission in SETTINGS_TABS
            if has_permission(self.membership, permission)
        ]

    def resolve_tab(self, requested):
        tabs = self.available_tabs()
        keys = [t["key"] for t in tabs]
        return requested if requested in keys else keys[0]

    def render_page(self, request, *, tab, business_form=None, account_form=None, status=200):
        context = {
            "tabs": self.available_tabs(),
            "active_tab": tab,
            "business_form": business_form or BusinessProfileForm(instance=self.organization),
            "account_form": account_form or AccountProfileForm(instance=request.user),
            "member_count": self.organization.memberships.count(),
            "integrations": [
                ("bi-facebook", "Facebook", "Connect your Pages so Replytic can watch for comments.", "facebook:index"),
                ("bi-whatsapp", "WhatsApp", "Move serious enquiries into a private chat.", ""),
                ("bi-stars", "AI assistant", "Understand questions and draft accurate replies.", ""),
            ],
        }
        return render(request, self.template_name, context, status=status)

    def get(self, request):
        return self.render_page(request, tab=self.resolve_tab(request.GET.get("tab", "")))

    def post(self, request):
        form_name = request.POST.get("form_name")
        if form_name == "business":
            if not has_permission(self.membership, "manage_business_profile"):
                raise PermissionDenied
            # The instance is the request's verified organization, never an id from the form.
            form = BusinessProfileForm(request.POST, request.FILES, instance=self.organization)
            if form.is_valid():
                form.save()
                messages.success(request, "Business profile updated successfully.")
                return redirect(f"{reverse('organizations:settings')}?tab=business")
            messages.error(request, "Please fix the highlighted fields.")
            return self.render_page(request, tab="business", business_form=form, status=400)

        if form_name == "account":
            form = AccountProfileForm(request.POST, instance=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Your account details were saved.")
                return redirect(f"{reverse('organizations:settings')}?tab=account")
            messages.error(request, "Please fix the highlighted fields.")
            return self.render_page(request, tab="account", account_form=form, status=400)

        messages.error(request, "Unknown form submitted.")
        return redirect("organizations:settings")

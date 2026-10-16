from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from organizations.permissions import has_permission


class OrganizationRequiredMixin(LoginRequiredMixin):
    """
    Base for every organization-scoped view.

    1. Anonymous users go to the login page.
    2. Users without a business are sent to create one.
    3. ``required_permission`` (if set) is checked against the *current*
       membership, so a 403 is raised for roles that lack it.

    Views should use ``self.organization`` / ``self.membership`` for every
    query and never read organization ids from the request.
    """

    required_permission = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.organization is None:
            messages.info(request, "Create your business to get started.")
            return redirect("organizations:create")
        if self.required_permission and not has_permission(request.membership, self.required_permission):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    @property
    def organization(self):
        return self.request.organization

    @property
    def membership(self):
        return self.request.membership

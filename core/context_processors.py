from organizations.permissions import permission_map
from organizations.services import user_memberships

from .navigation import build_navigation


def workspace(request):
    """Expose the current organization, switcher list, permissions and nav."""
    membership = getattr(request, "membership", None)
    organization = getattr(request, "organization", None)
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    match = getattr(request, "resolver_match", None)
    view_name = match.view_name if match else ""
    memberships = list(user_memberships(user)) if organization else []
    return {
        "current_organization": organization,
        "current_membership": membership,
        "user_memberships": memberships,
        "can": permission_map(membership),
        "nav_sections": build_navigation(membership, view_name),
    }

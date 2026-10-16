"""Business logic for organizations, membership and current-organization lookup."""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Organization, OrganizationMembership

SESSION_KEY = "current_organization_id"
Role = OrganizationMembership.Role


class MembershipError(Exception):
    """Raised when a team change is not allowed. The message is user-facing."""


@transaction.atomic
def create_organization(*, owner, name, **extra):
    """Create an organization and make ``owner`` its OWNER."""
    organization = Organization.objects.create(name=name.strip(), **extra)
    membership = OrganizationMembership.objects.create(
        user=owner, organization=organization, role=Role.OWNER
    )
    return organization, membership


def user_memberships(user):
    """Active-organization memberships for ``user`` (empty for anonymous)."""
    if not getattr(user, "is_authenticated", False):
        return OrganizationMembership.objects.none()
    return (
        OrganizationMembership.objects.filter(user=user, organization__is_active=True)
        .select_related("organization")
        .order_by("created_at")
    )


def resolve_current_membership(user, session):
    """
    Work out which membership the request is acting under.

    The organization id stored in the session is treated as a *hint*: it is
    only honoured when the user really is a member of that active
    organization. Anything else falls back to the user's first membership.
    """
    memberships = list(user_memberships(user))
    if not memberships:
        session.pop(SESSION_KEY, None)
        return None

    selected_id = session.get(SESSION_KEY)
    if selected_id:
        for membership in memberships:
            if str(membership.organization_id) == str(selected_id):
                return membership

    fallback = memberships[0]
    session[SESSION_KEY] = str(fallback.organization_id)
    return fallback


def switch_organization(*, user, session, organization_id):
    """
    Select ``organization_id`` as current. Returns the membership, or None
    when the user does not belong to it (session is left untouched).
    """
    try:
        membership = user_memberships(user).filter(organization_id=organization_id).first()
    except (ValueError, TypeError, ValidationError):  # malformed UUID
        membership = None
    if membership is None:
        return None
    session[SESSION_KEY] = str(membership.organization_id)
    return membership


def _active_owner_count(organization):
    return OrganizationMembership.objects.filter(
        organization=organization, role=Role.OWNER, user__is_active=True
    ).count()


@transaction.atomic
def change_member_role(*, membership, new_role):
    if new_role not in Role.values:
        raise MembershipError("Choose a valid role.")
    if membership.role == new_role:
        return membership
    if membership.role == Role.OWNER and _active_owner_count(membership.organization) <= 1:
        raise MembershipError("Every business needs at least one owner.")
    membership.role = new_role
    membership.save(update_fields=["role", "updated_at"])
    return membership


@transaction.atomic
def remove_member(*, membership):
    if membership.role == Role.OWNER and _active_owner_count(membership.organization) <= 1:
        raise MembershipError("You can't remove the only owner of a business.")
    membership.delete()

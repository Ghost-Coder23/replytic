"""
Role-based permissions.

Permissions are plain strings mapped to the roles that hold them. Keep this
the single source of truth: templates, views and the navigation all read it.
"""
from .models import OrganizationMembership

Role = OrganizationMembership.Role

ALL_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.AGENT})
MANAGERS = frozenset({Role.OWNER, Role.ADMIN})
OWNER_ONLY = frozenset({Role.OWNER})

PERMISSIONS = {
    # Everyday work
    "view_dashboard": ALL_ROLES,
    "view_comments": ALL_ROLES,
    "manage_leads": ALL_ROLES,
    "reply_to_comments": ALL_ROLES,
    "view_team": ALL_ROLES,
    # Business configuration
    "manage_automations": MANAGERS,
    "manage_knowledge": MANAGERS,
    "manage_integrations": MANAGERS,
    "view_analytics": MANAGERS,
    "manage_business_profile": MANAGERS,
    # People and money
    "manage_team": MANAGERS,
    "change_member_roles": OWNER_ONLY,
    "view_billing": MANAGERS,
    "manage_billing": OWNER_ONLY,
}


def has_permission(membership, permission):
    """True when ``membership`` (or None) holds ``permission``."""
    if membership is None:
        return False
    return membership.role in PERMISSIONS.get(permission, frozenset())


def permission_map(membership):
    """{permission: bool} for use in templates as ``can.manage_team``."""
    return {name: has_permission(membership, name) for name in PERMISSIONS}


def can_change_member_role(actor, target):
    """Only owners change roles; nobody changes their own role."""
    return (
        has_permission(actor, "change_member_roles")
        and actor.pk != target.pk
        and actor.organization_id == target.organization_id
    )


def can_remove_member(actor, target):
    """Owners remove anyone else; admins remove agents only."""
    if actor.organization_id != target.organization_id or actor.pk == target.pk:
        return False
    if actor.role == Role.OWNER:
        return True
    if actor.role == Role.ADMIN:
        return target.role == Role.AGENT
    return False

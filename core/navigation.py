"""Sidebar structure. Active state is derived from URL names, never hardcoded."""
from dataclasses import dataclass, field

from organizations.permissions import has_permission


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    icon: str
    permission: str = "view_dashboard"
    soon: bool = False
    also_active_for: tuple = field(default_factory=tuple)


NAV_SECTIONS = [
    ("Workspace", [
        NavItem("Dashboard", "dashboard:index", "bi-grid-1x2", "view_dashboard"),
        NavItem("Comments", "comments:index", "bi-chat-square-text", "view_comments"),
        NavItem("Leads", "dashboard:leads", "bi-person-check", "manage_leads", soon=True),
    ]),
    ("Automation", [
        NavItem("Automations", "automations:index", "bi-lightning-charge", "manage_automations", also_active_for=("automations:create", "automations:edit")),
        NavItem("Knowledge", "dashboard:knowledge", "bi-journal-text", "manage_knowledge", soon=True),
        NavItem("Facebook", "facebook:index", "bi-facebook", "manage_integrations"),
    ]),
    ("Insights", [
        NavItem("Analytics", "dashboard:analytics", "bi-bar-chart-line", "view_analytics", soon=True),
    ]),
    ("Business", [
        NavItem("Team", "organizations:team", "bi-people", "view_team"),
        NavItem("Billing", "dashboard:billing", "bi-credit-card", "view_billing", soon=True),
        NavItem(
            "Settings", "organizations:settings", "bi-gear", "view_dashboard",
            also_active_for=("accounts:password_change",),
        ),
    ]),
]


def build_navigation(membership, current_view_name):
    """Return sections visible to ``membership`` with ``active`` flags set."""
    sections = []
    for title, items in NAV_SECTIONS:
        visible = []
        for item in items:
            if not has_permission(membership, item.permission):
                continue
            active = current_view_name == item.url_name or current_view_name in item.also_active_for
            visible.append({
                "label": item.label,
                "url_name": item.url_name,
                "icon": item.icon,
                "soon": item.soon,
                "active": active,
            })
        if visible:
            sections.append({"title": title, "items": visible})
    return sections

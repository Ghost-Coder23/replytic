"""Dashboard data. Nothing here is fabricated: metrics are 0 until real data exists."""
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from organizations.models import OrganizationMembership


def _organization_zone(organization):
    try:
        return ZoneInfo(organization.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.get_default_timezone()


def greeting_for(organization, now=None):
    """Greeting based on the business's local time."""
    now = now or timezone.now()
    hour = now.astimezone(_organization_zone(organization)).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def display_name(user):
    return user.first_name or (user.email or user.username).split("@")[0]


def _start_of_today(organization):
    zone = _organization_zone(organization)
    local_now = timezone.now().astimezone(zone)
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)


def metric_cards(organization):
    """Real counts from stored comments and replies. Potential leads stay 0 until lead capture exists."""
    from comments.models import Comment, Reply

    start = _start_of_today(organization)
    comments_today = Comment.objects.filter(organization=organization, is_deleted=False, is_from_page=False, commented_at__gte=start).count()
    automated = Reply.objects.filter(
        organization=organization, source=Reply.Source.AUTO, status=Reply.Status.SENT, sent_at__gte=start,
    ).count()
    attention = Comment.objects.filter(organization=organization, is_deleted=False, status__in=Comment.NEEDS_ATTENTION).count()
    return [
        {"label": "Comments today", "value": comments_today, "icon": "bi-chat-square-text", "tone": "teal",
         "note": "Since midnight, your time" if comments_today else "Starts counting once Facebook is connected"},
        {"label": "Automated replies", "value": automated, "icon": "bi-lightning-charge", "tone": "sun",
         "note": "Sent automatically today" if automated else "Starts with automatic replies"},
        {"label": "Potential leads", "value": 0, "icon": "bi-person-check", "tone": "ink", "note": "Lead capture is coming soon"},
        {"label": "Needs attention", "value": attention, "icon": "bi-flag", "tone": "rose",
         "note": "Waiting for a person" if attention else "Nothing waiting for you"},
    ]


def onboarding_checklist(organization):
    """Checklist items. Only 'add business information' is live in Phase 1."""
    business_info_done = bool(organization.description.strip()) and bool(
        organization.phone or organization.email or organization.address
    )
    items = [
        {"label": "Create your account", "state": "done"},
        {"label": "Create your business", "state": "done"},
        {
            "label": "Connect Facebook Page",
            "state": "done" if organization.facebook_pages.filter(status="CONNECTED").exists() else "todo",
            "url_name": "facebook:index",
            "hint": "Choose the Page Replytic should watch",
            "requires": "manage_integrations",
        },
        {
            "label": "Add business information",
            "state": "done" if business_info_done else "todo",
            "url_name": "organizations:settings",
            "hint": "Add a description and one way to reach you",
        },
        {"label": "Add FAQs", "state": "soon", "hint": "Knowledge base is coming"},
        {
            "label": "Create first automation",
            "state": "done" if organization.automation_rules.exists() else "todo",
            "url_name": "automations:create",
            "hint": "Answer a common question automatically",
            "requires": "manage_automations",
        },
        {
            "label": "Enable automation",
            "state": "done" if organization.automation_rules.filter(is_active=True).exists() and organization.facebook_pages.filter(status="CONNECTED").exclude(automation_mode="OFF").exists() else "todo",
            "url_name": "automations:index",
            "hint": "Pages suggest replies by default. Choose how each behaves",
            "requires": "manage_automations",
        },
    ]
    completed = sum(1 for item in items if item["state"] == "done")
    return {
        "items": items,
        "completed": completed,
        "total": len(items),
        "percent": round(completed / len(items) * 100),
    }


def recent_activity(organization, membership):
    """Real events derived from stored data, newest first."""
    events = [{
        "icon": "bi-building",
        "text": f"{organization.name} was set up on Replytic",
        "at": organization.created_at,
    }, {
        "icon": "bi-person-check",
        "text": f"You joined as {membership.get_role_display()}",
        "at": membership.created_at,
    }]
    others = (
        OrganizationMembership.objects.filter(organization=organization)
        .exclude(pk=membership.pk)
        .select_related("user")
        .order_by("-created_at")[:3]
    )
    for other in others:
        name = other.user.get_full_name() or other.user.email
        events.append({"icon": "bi-person-plus", "text": f"{name} joined as {other.get_role_display()}", "at": other.created_at})
    if organization.updated_at - organization.created_at > timedelta(minutes=1):
        events.append({"icon": "bi-pencil-square", "text": "Business profile was updated", "at": organization.updated_at})
    from comments.models import Comment, Reply

    for comment in Comment.objects.filter(organization=organization, is_from_page=False, is_deleted=False).order_by("-commented_at")[:3]:
        events.append({"icon": "bi-chat-square-text", "text": f"{comment.author_name or 'Someone'} commented on {comment.page.name}", "at": comment.commented_at})
    for reply in Reply.objects.filter(organization=organization, status=Reply.Status.SENT).order_by("-sent_at")[:3]:
        who = "Replytic replied automatically" if reply.source == Reply.Source.AUTO else "A reply was posted"
        events.append({"icon": "bi-reply", "text": f"{who} to {reply.comment.author_name or 'a customer'}", "at": reply.sent_at})
    events.sort(key=lambda event: event["at"], reverse=True)
    return events[:6]


def build_dashboard_context(*, user, organization, membership):
    return {
        "greeting": greeting_for(organization),
        "first_name": display_name(user),
        "metrics": metric_cards(organization),
        "checklist": onboarding_checklist(organization),
        "activity": recent_activity(organization, membership),
    }

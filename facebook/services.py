"""
Facebook / Meta integration logic. Views stay thin and call into here.

Security notes
* OAuth ``state`` is random, bound to the user and organization, single-use and expires.
* Tokens are encrypted at rest (see ``crypto``) and never shown in templates or logs.
* A Facebook Page can be CONNECTED to only one organization at a time.
* Webhook bodies are only trusted after an HMAC-SHA256 check with the app secret.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from . import graph
from .models import FacebookConnection, FacebookDeletionRequest, FacebookPage, FacebookWebhookEvent

logger = logging.getLogger(__name__)

OAUTH_SESSION_KEY = "facebook_oauth_state"
OAUTH_STATE_TTL_SECONDS = 600
REQUIRED_PERMISSIONS = ("pages_show_list", "pages_manage_metadata")
MAX_PAGE_REQUESTS = 10


class FacebookIntegrationError(Exception):
    """A problem with a user-facing message."""


# ---------------------------------------------------------------- configuration

def is_configured():
    return bool(settings.META_APP_ID and settings.META_APP_SECRET)


def webhook_is_configured():
    return bool(settings.META_APP_SECRET and settings.META_WEBHOOK_VERIFY_TOKEN)


def callback_url():
    return f"{settings.SITE_URL}{reverse('facebook:callback')}"


def webhook_url():
    return f"{settings.SITE_URL}{reverse('facebook:webhook')}"


def setup_checklist():
    """What is still missing in the environment, for the admin-facing setup card."""
    return [
        ("META_APP_ID and META_APP_SECRET", bool(settings.META_APP_ID and settings.META_APP_SECRET)),
        ("META_WEBHOOK_VERIFY_TOKEN", bool(settings.META_WEBHOOK_VERIFY_TOKEN)),
        ("SITE_URL is a public https address (needed for webhooks)", settings.SITE_URL.startswith("https://")),
    ]


# ------------------------------------------------------------------------ OAuth

def start_oauth(*, session, user, organization):
    """Create a one-time state, remember it in the session, return the Meta login URL."""
    if not is_configured():
        raise FacebookIntegrationError("Facebook isn't set up on this Replytic installation yet.")
    state = secrets.token_urlsafe(32)
    session[OAUTH_SESSION_KEY] = {
        "state": state, "user": user.pk, "org": str(organization.pk), "ts": int(time.time()),
    }
    return graph.login_dialog_url(state=state, redirect_uri=callback_url())


def _consume_state(session, *, state, user, organization):
    saved = session.pop(OAUTH_SESSION_KEY, None)  # single use, even on failure
    if not saved or not state:
        raise FacebookIntegrationError("That Facebook sign-in expired. Please start again.")
    if not hmac.compare_digest(str(saved.get("state", "")), str(state)):
        raise FacebookIntegrationError("We couldn't verify that Facebook sign-in. Please start again.")
    if int(time.time()) - int(saved.get("ts", 0)) > OAUTH_STATE_TTL_SECONDS:
        raise FacebookIntegrationError("That Facebook sign-in took too long. Please start again.")
    if saved.get("user") != user.pk or saved.get("org") != str(organization.pk):
        raise FacebookIntegrationError("You switched business during sign-in. Please start again.")


def complete_oauth(*, session, user, organization, code, state):
    """Finish the OAuth dance and store the connection + available Pages."""
    _consume_state(session, state=state, user=user, organization=organization)
    if not code:
        raise FacebookIntegrationError("Facebook didn't return an authorisation code.")

    credentials = {"client_id": settings.META_APP_ID, "client_secret": settings.META_APP_SECRET}
    try:
        short = graph.get("oauth/access_token", redirect_uri=callback_url(), code=code, **credentials)
        long_lived = graph.get(
            "oauth/access_token", grant_type="fb_exchange_token",
            fb_exchange_token=short["access_token"], **credentials,
        )
        token = long_lived.get("access_token") or short["access_token"]
        expires_in = long_lived.get("expires_in")
        profile = graph.get("me", fields="id,name", access_token=token)
        granted = _granted_permissions(token)
    except (KeyError, TypeError):
        raise FacebookIntegrationError("Facebook returned an unexpected response. Please try again.") from None
    except graph.GraphAPIError as error:
        raise FacebookIntegrationError(f"Facebook couldn't complete the sign-in: {error}") from None

    missing = [p for p in REQUIRED_PERMISSIONS if p not in granted]
    if missing:
        raise FacebookIntegrationError(
            "Replytic needs permission to list and manage your Pages. "
            "Please start again and approve every requested permission."
        )

    connection, _ = FacebookConnection.objects.update_or_create(
        organization=organization, facebook_user_id=str(profile["id"]),
        defaults={
            "connected_by": user,
            "facebook_user_name": profile.get("name", ""),
            "access_token": token,
            "token_expires_at": timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None,
            "granted_scopes": ",".join(sorted(granted)),
            "status": FacebookConnection.Status.ACTIVE,
        },
    )
    sync_pages(connection)
    return connection


def _granted_permissions(token):
    data = graph.get("me/permissions", access_token=token).get("data", [])
    return {item["permission"] for item in data if item.get("status") == "granted"}


# ------------------------------------------------------------------------ pages

def sync_pages(connection):
    """Refresh the list of Pages this Facebook user can manage."""
    token = connection.access_token
    if not token:
        _mark_needs_reconnect(connection)
        raise FacebookIntegrationError("Replytic lost access to this Facebook account. Please reconnect.")

    try:
        result = graph.get("me/accounts", fields="id,name,category,access_token", limit=100, access_token=token)
        pages = list(result.get("data", []))
        for _ in range(MAX_PAGE_REQUESTS):
            next_url = result.get("paging", {}).get("next")
            if not next_url:
                break
            result = graph.get_url(next_url)
            pages.extend(result.get("data", []))
    except graph.GraphAPIError as error:
        if error.is_auth_error:
            _mark_needs_reconnect(connection)
            raise FacebookIntegrationError("Facebook access has expired or was revoked. Please reconnect.") from None
        raise FacebookIntegrationError(f"Couldn't load your Pages: {error}") from None

    seen = set()
    with transaction.atomic():
        for item in pages:
            page_id = str(item["id"])
            seen.add(page_id)
            page, created = FacebookPage.objects.get_or_create(
                organization=connection.organization, page_id=page_id,
                defaults={"connection": connection, "name": item.get("name", page_id)},
            )
            page.connection = connection
            page.name = item.get("name", page.name)
            page.category = item.get("category", "")
            page.access_token = item.get("access_token", "")
            if page.status in (FacebookPage.Status.ERROR, FacebookPage.Status.DISCONNECTED):
                page.status = FacebookPage.Status.AVAILABLE
                page.last_error = ""
            page.save()
        stale = connection.pages.exclude(page_id__in=seen)
        for gone in stale.filter(status=FacebookPage.Status.AVAILABLE):
            _detach_page(gone)  # keep the row (history), just stop offering it
        stale.filter(status=FacebookPage.Status.CONNECTED).update(
            status=FacebookPage.Status.ERROR, last_error="This Page is no longer available to the connected Facebook account.",
        )
    return list(connection.pages.all())


def _mark_needs_reconnect(connection):
    connection.status = FacebookConnection.Status.NEEDS_RECONNECT
    connection.save(update_fields=["status", "updated_at"])


def connect_page(page):
    """Subscribe to the Page's feed webhooks and mark it connected."""
    if page.is_connected:
        return page
    if FacebookPage.objects.filter(page_id=page.page_id, status=FacebookPage.Status.CONNECTED).exclude(pk=page.pk).exists():
        raise FacebookIntegrationError("This Page is already connected to another business on Replytic.")
    if not page.access_token:
        raise FacebookIntegrationError("Replytic can't access this Page right now. Refresh your Pages or reconnect Facebook.")
    if not webhook_is_configured():
        raise FacebookIntegrationError("Webhooks aren't configured yet, so Replytic can't watch this Page. Ask your administrator to set the Meta webhook variables.")

    try:
        graph.post(f"{page.page_id}/subscribed_apps", subscribed_fields="feed", access_token=page.access_token)
    except graph.GraphAPIError as error:
        page.status = FacebookPage.Status.ERROR
        page.last_error = str(error)[:500]
        page.save(update_fields=["status", "last_error", "updated_at"])
        raise FacebookIntegrationError(f"Facebook wouldn't let Replytic watch this Page: {error}") from None

    page.status = FacebookPage.Status.CONNECTED
    page.last_error = ""
    page.connected_at = timezone.now()
    try:
        with transaction.atomic():
            page.save()
    except IntegrityError:
        raise FacebookIntegrationError("This Page is already connected to another business on Replytic.") from None
    return page


def disconnect_page(page):
    """Stop watching a Page (best-effort unsubscribe on Meta's side)."""
    if page.is_connected and page.access_token:
        try:
            graph.delete(f"{page.page_id}/subscribed_apps", access_token=page.access_token)
        except graph.GraphAPIError:
            logger.warning("Could not unsubscribe page %s from webhooks", page.page_id)
    page.status = FacebookPage.Status.AVAILABLE
    page.connected_at = None
    page.last_error = ""
    page.save(update_fields=["status", "connected_at", "last_error", "updated_at"])
    return page


def _detach_page(page):
    """Erase a Page's token and unlink it from its Facebook account, keeping the row for history."""
    page.status = FacebookPage.Status.DISCONNECTED
    page.access_token = ""
    page.connection = None
    page.connected_at = None
    page.save()


def disconnect_connection(connection, *, unsubscribe=True):
    """
    Forget a Facebook account: stop watching its Pages and delete every stored token.

    Page rows are KEPT (as DISCONNECTED, with no token). Comments, replies and rules point at
    them, so deleting a Page would silently wipe the business's comment history.
    """
    for page in list(connection.pages.all()):
        if unsubscribe and page.is_connected:
            disconnect_page(page)
        _detach_page(page)
    connection.delete()


def organization_summary(organization):
    """Everything the Facebook page needs, always scoped to one organization."""
    connections = list(organization.facebook_connections.prefetch_related("pages"))
    pages = list(organization.facebook_pages.exclude(status=FacebookPage.Status.DISCONNECTED).select_related("connection"))
    events = organization.facebook_webhook_events
    last_event = events.first()
    return {
        "connections": connections,
        "pages": pages,
        "connected_pages": [p for p in pages if p.is_connected],
        "available_pages": [p for p in pages if not p.is_connected],
        "missing_reply_permission": any(
            "pages_manage_engagement" not in (c.granted_scopes or "").split(",") for c in connections
        ),
        "needs_reconnect": any(c.status == FacebookConnection.Status.NEEDS_RECONNECT for c in connections),
        "event_count": events.count(),
        "last_event_at": last_event.received_at if last_event else None,
    }


# --------------------------------------------------------------------- webhooks

def verify_signature(raw_body: bytes, header: str) -> bool:
    """Check Meta's ``X-Hub-Signature-256`` header (sha256=<hex hmac of body>)."""
    secret = settings.META_APP_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def verify_challenge(params):
    """Answer Meta's subscription handshake. Returns the challenge string or None."""
    token = settings.META_WEBHOOK_VERIFY_TOKEN
    if not token or params.get("hub.mode") != "subscribe":
        return None
    if not hmac.compare_digest(str(params.get("hub.verify_token", "")), token):
        return None
    return params.get("hub.challenge", "")


def record_webhook_payload(payload):
    """
    Store each change for a CONNECTED Page under the owning organization.
    Unknown Pages are ignored. Retries from Meta are de-duplicated.
    Returns how many new events were stored.
    """
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return 0
    created = 0
    for entry in payload.get("entry", []):
        page_id = str(entry.get("id", ""))
        page = FacebookPage.objects.filter(page_id=page_id, status=FacebookPage.Status.CONNECTED).select_related("organization").first()
        if page is None:
            continue
        for change in entry.get("changes", []):
            fingerprint = json.dumps([page_id, entry.get("time"), change], sort_keys=True, default=str)
            _, was_created = FacebookWebhookEvent.objects.get_or_create(
                dedupe_key=hashlib.sha256(fingerprint.encode()).hexdigest(),
                defaults={
                    "organization": page.organization, "page": page, "facebook_page_id": page_id,
                    "field": str(change.get("field", ""))[:64], "payload": change,
                },
            )
            created += was_created
    return created


# ------------------------------------------- deauthorize / data deletion (Meta callbacks)

def parse_signed_request(signed_request):
    """
    Verify and decode the ``signed_request`` Meta POSTs to the deauthorize and
    data-deletion callbacks. Returns the payload dict, or None if it is invalid.
    """
    secret = settings.META_APP_SECRET
    if not secret or not signed_request or "." not in signed_request:
        return None
    encoded_sig, _, encoded_payload = signed_request.partition(".")

    def b64(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    try:
        signature = b64(encoded_sig)
        payload = json.loads(b64(encoded_payload))
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    if str(payload.get("algorithm", "")).upper() != "HMAC-SHA256" or not payload.get("user_id"):
        return None
    return payload


def forget_facebook_user(facebook_user_id):
    """
    Delete every connection (and its encrypted tokens and Pages) for a Facebook user, across
    all businesses. No network calls: the user has already revoked access on Facebook's side.
    Returns how many connections were removed.
    """
    connections = list(FacebookConnection.objects.filter(facebook_user_id=str(facebook_user_id)))
    for connection in connections:
        disconnect_connection(connection, unsubscribe=False)
    # If this person also commented on a connected Page, erase what we hold about them.
    from comments.models import Comment
    Comment.objects.filter(author_id=str(facebook_user_id)).update(author_id="", author_name="", message="")
    return len(connections)


def create_deletion_request(facebook_user_id):
    removed = forget_facebook_user(facebook_user_id)
    return FacebookDeletionRequest.objects.create(
        confirmation_code=secrets.token_urlsafe(16),
        facebook_user_hash=hashlib.sha256(str(facebook_user_id).encode()).hexdigest(),
        connections_removed=removed,
        completed_at=timezone.now(),
    )


def deletion_status_url(request_record):
    return f"{settings.SITE_URL}{reverse('facebook:deletion_status')}?code={request_record.confirmation_code}"


def deauthorize_url():
    return f"{settings.SITE_URL}{reverse('facebook:deauthorize')}"


def data_deletion_url():
    return f"{settings.SITE_URL}{reverse('facebook:data_deletion')}"

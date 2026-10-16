"""Shared helpers for Phase 3 tests."""
import itertools
import urllib.parse
import uuid
from datetime import timedelta

from django.utils import timezone

from automations.models import AutomationRule
from comments import services
from facebook import graph
from facebook.models import FacebookConnection, FacebookPage, FacebookWebhookEvent

from .helpers import make_org_with_owner

_counter = itertools.count(1)


def make_page(org, owner, page_id="111", name="Alpha Page", mode="DRAFT", scopes="pages_show_list,pages_manage_metadata,pages_manage_engagement", connection=None):
    connection = connection or FacebookConnection.objects.create(
        organization=org, connected_by=owner, facebook_user_id=f"fb-{uuid.uuid4().hex[:6]}",
        facebook_user_name="Owner", access_token="USER-TOKEN", granted_scopes=scopes,
    )
    return FacebookPage.objects.create(
        organization=org, connection=connection, page_id=page_id, name=name, access_token=f"PAGE-TOKEN-{page_id}",
        status=FacebookPage.Status.CONNECTED, automation_mode=mode,
    )


def make_rule(org, keywords="price\nhow much", template="Hi {first_name}, thanks for asking!", **extra):
    defaults = dict(organization=org, name="Price questions", keywords=keywords, reply_template=template, action="REPLY")
    defaults.update(extra)
    return AutomationRule.objects.create(**defaults)


def comment_change(comment_id=None, message="How much is this?", author_id="u1", author_name="Tendai Moyo", verb="add", post_id="p1", created=None, parent_id=None, item="comment"):
    comment_id = comment_id or f"c{next(_counter)}"
    value = {"item": item, "verb": verb, "comment_id": comment_id, "post_id": post_id, "parent_id": parent_id or post_id,
             "from": {"id": author_id, "name": author_name}, "created_time": created or int(timezone.now().timestamp())}
    if message is not None:
        value["message"] = message
    return {"field": "feed", "value": value}


def make_event(page, change):
    return FacebookWebhookEvent.objects.create(
        organization=page.organization, page=page, facebook_page_id=page.page_id, field="feed",
        payload=change, dedupe_key=uuid.uuid4().hex + uuid.uuid4().hex[:32],
    )


def ingest(page, **kwargs):
    """Store an event and process it. Returns the resulting Comment (or None)."""
    change = comment_change(**kwargs)
    event = make_event(page, change)
    services.process_event(event)
    from comments.models import Comment
    return Comment.objects.filter(facebook_comment_id=change["value"]["comment_id"]).first()


class FakeReplyGraph:
    """Replacement for facebook.graph._open covering comment reads and reply posts."""

    def __init__(self, fail_post=None, comment_details=None):
        self.calls = []
        self.fail_post = fail_post
        self.comment_details = comment_details or {}

    def __call__(self, url, *, data=None, method="GET"):
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.split("/", 2)[2]
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((method, path, query, data))
        if method == "POST" and path.endswith("/comments"):
            if self.fail_post:
                raise self.fail_post
            return {"id": f"reply-{len(self.calls)}"}
        if method == "GET":
            return self.comment_details
        raise AssertionError(f"Unexpected call {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

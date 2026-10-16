"""
The comment engine: webhook events -> comments -> rules -> replies.

Safety rules baked in here (not in the UI):
* Comments are de-duplicated on Facebook's comment id.
* At most one live reply per comment (database constraint + atomic claim before sending).
* Replies are only posted automatically when the Page is in AUTO mode, the comment is recent,
  the hourly limit is not reached and no placeholder is missing. Otherwise a draft is created.
* Replytic never answers its own (Page) comments, which prevents reply loops.
"""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from automations import engine
from automations.models import AutomationRule
from facebook import graph
from facebook.models import FacebookConnection, FacebookPage, FacebookWebhookEvent

from .models import Comment, Reply

logger = logging.getLogger(__name__)

MAX_EVENT_ATTEMPTS = 5
STUCK_SENDING_AFTER = timedelta(minutes=5)
REPLY_PERMISSION = "pages_manage_engagement"


class ReplyError(Exception):
    """A reply can't be sent. The message is safe to show to users."""


# ------------------------------------------------------------------ ingestion

def parse_comment_change(change):
    """Extract a comment description from a Meta ``feed`` change, or None if it isn't a comment."""
    if change.get("field") != "feed":
        return None
    value = change.get("value") or {}
    if value.get("item") != "comment" or not value.get("comment_id"):
        return None
    sender = value.get("from") or {}
    created = value.get("created_time")
    if isinstance(created, (int, float)):
        commented_at = datetime.fromtimestamp(created, tz=dt_timezone.utc)
    else:
        commented_at = parse_datetime(str(created)) if created else None
    return {
        "verb": value.get("verb", "add"),
        "comment_id": str(value["comment_id"]),
        "post_id": str(value.get("post_id", "")),
        "parent_id": str(value.get("parent_id", "")),
        "author_id": str(sender.get("id", "")),
        "author_name": sender.get("name", ""),
        "message": value.get("message"),
        "commented_at": commented_at,
    }


def _fetch_missing_details(page, info):
    """Meta sometimes omits the text. Ask the Graph API for it (needs pages_read_user_content)."""
    if info["message"] is not None and info["author_id"]:
        return info
    token = page.access_token
    if not token:
        return info
    try:
        data = graph.get(info["comment_id"], fields="message,from{id,name},created_time", access_token=token)
    except graph.GraphAPIError as error:
        logger.warning("Could not fetch comment %s: %s", info["comment_id"], error)
        return info
    sender = data.get("from") or {}
    info["message"] = data.get("message", info["message"] or "")
    info["author_id"] = info["author_id"] or str(sender.get("id", ""))
    info["author_name"] = info["author_name"] or sender.get("name", "")
    if not info["commented_at"] and data.get("created_time"):
        info["commented_at"] = parse_datetime(data["created_time"])
    return info


def ingest_event(event):
    """Turn one stored webhook event into a Comment (idempotent). Returns the Comment or None."""
    info = parse_comment_change(event.payload)
    page = event.page
    if info is None or page is None or page.organization_id != event.organization_id:
        return None

    if info["verb"] in ("remove", "delete", "hide"):
        Comment.objects.filter(facebook_comment_id=info["comment_id"], organization=event.organization).update(is_deleted=True)
        return None

    if info["verb"] in ("edited", "edit"):
        updated = Comment.objects.filter(
            facebook_comment_id=info["comment_id"], organization=event.organization, status__in=Comment.NEEDS_ATTENTION,
        ).update(message=info["message"] or "")
        if updated:
            return None  # an edit never triggers a second automatic reply

    if Comment.objects.filter(facebook_comment_id=info["comment_id"]).exists():
        return None

    info = _fetch_missing_details(page, info)
    is_from_page = bool(info["author_id"]) and info["author_id"] == page.page_id
    try:
        with transaction.atomic():
            comment = Comment.objects.create(
                organization=event.organization, page=page, facebook_comment_id=info["comment_id"],
                post_id=info["post_id"], parent_id=info["parent_id"],
                author_id=info["author_id"], author_name=info["author_name"],
                message=info["message"] or "", commented_at=info["commented_at"] or event.received_at,
                is_from_page=is_from_page,
                status=Comment.Status.IGNORED if is_from_page else Comment.Status.NEW,
                decision_note="Written by your own Page." if is_from_page else "",
            )
    except IntegrityError:
        return None  # lost a race with another worker; the comment already exists
    if not is_from_page:
        automate(comment)
    return comment


def process_event(event):
    try:
        ingest_event(event)
    except Exception as error:  # keep the queue moving; retry a few times then give up
        logger.exception("Failed to process webhook event %s", event.pk)
        event.attempts += 1
        event.error = str(error)[:500]
        if event.attempts >= MAX_EVENT_ATTEMPTS:
            event.processed_at = timezone.now()
        event.save(update_fields=["attempts", "error", "processed_at"])
        return False
    event.processed_at = timezone.now()
    event.error = ""
    event.save(update_fields=["processed_at", "error"])
    return True


def process_pending_events(*, organization=None, limit=200):
    """Process unprocessed feed events (optionally for one organization). Returns the count handled."""
    queryset = FacebookWebhookEvent.objects.filter(processed_at__isnull=True, attempts__lt=MAX_EVENT_ATTEMPTS).select_related("page", "organization")
    if organization is not None:
        queryset = queryset.filter(organization=organization)
    handled = 0
    for event in queryset.order_by("received_at")[:limit]:
        handled += bool(process_event(event))
    return handled


# ----------------------------------------------------------------- automation

def auto_replies_in_last_hour(page):
    since = timezone.now() - timedelta(hours=1)
    return Reply.objects.filter(
        page=page, source=Reply.Source.AUTO, status__in=[Reply.Status.SENT, Reply.Status.SENDING], created_at__gte=since,
    ).count()


def automate(comment):
    """Apply the organization's rules to a new comment, honouring the Page's automation mode."""
    page = comment.page
    if page.automation_mode == FacebookPage.AutomationMode.OFF:
        comment.decision_note = "Automation is off for this Page."
        comment.save(update_fields=["decision_note", "updated_at"])
        return

    rules = list(AutomationRule.objects.filter(organization=comment.organization, is_active=True))
    decision = engine.decide(
        message=comment.message, author_name=comment.author_name,
        page=page, organization=comment.organization, rules=rules,
    )
    if decision is None:
        comment.decision_note = "No rule matched. A person should take a look."
        comment.save(update_fields=["decision_note", "updated_at"])
        return

    AutomationRule.objects.filter(pk=decision.rule.pk).update(match_count=F("match_count") + 1)
    comment.matched_rule = decision.rule
    if decision.action == AutomationRule.Action.IGNORE:
        comment.status = Comment.Status.IGNORED
        comment.decision_note = f"Ignored by rule “{decision.rule.name}”."
    elif decision.action == AutomationRule.Action.FLAG:
        comment.status = Comment.Status.FLAGGED
        comment.decision_note = f"Flagged by rule “{decision.rule.name}”."
    else:
        comment.save()
        _apply_reply_decision(comment, decision)
        return
    comment.save()


def _apply_reply_decision(comment, decision):
    page = comment.page
    reason = None
    if page.automation_mode != FacebookPage.AutomationMode.AUTO:
        reason = "Waiting for approval (this Page is set to suggest replies)."
    elif decision.missing:
        reason = "Missing " + ", ".join("{" + m + "}" for m in decision.missing) + " in your business profile, so this needs a check."
    elif timezone.now() - comment.commented_at > timedelta(hours=settings.AUTO_REPLY_MAX_COMMENT_AGE_HOURS):
        reason = "This comment is old, so it wasn't answered automatically."
    elif auto_replies_in_last_hour(page) >= page.max_auto_replies_per_hour:
        reason = "The hourly automatic-reply limit was reached, so this waits for approval."

    source = Reply.Source.APPROVED if reason else Reply.Source.AUTO
    try:
        with transaction.atomic():
            reply = Reply.objects.create(
                organization=comment.organization, comment=comment, page=page,
                message=decision.text, source=source, rule=decision.rule,
            )
    except IntegrityError:
        return  # a live reply already exists: never create a second one
    if reason:
        comment.status = Comment.Status.DRAFTED
        comment.decision_note = f"Rule “{decision.rule.name}”: {reason}"
        comment.save(update_fields=["status", "decision_note", "updated_at"])
        return
    comment.decision_note = f"Answered automatically by rule “{decision.rule.name}”."
    comment.save(update_fields=["decision_note", "updated_at"])
    try:
        send_reply(reply)
    except ReplyError:
        pass  # state (FAILED + message) is already recorded by send_reply


# -------------------------------------------------------------------- replying

def _fail(reply, message, *, status=Reply.Status.FAILED):
    reply.status = status
    reply.error = message[:500]
    reply.save(update_fields=["status", "error", "updated_at"])
    Comment.objects.filter(pk=reply.comment_id).update(status=Comment.Status.FAILED, updated_at=timezone.now())
    raise ReplyError(message)


def send_reply(reply):
    """
    Post a DRAFT reply publicly on Facebook.

    The DRAFT -> SENDING switch is one atomic UPDATE, so if two requests race only one wins
    and the other gets a ReplyError. Nothing is ever posted twice.
    """
    claimed = Reply.objects.filter(pk=reply.pk, status=Reply.Status.DRAFT).update(
        status=Reply.Status.SENDING, sending_started_at=timezone.now(), error="",
    )
    if not claimed:
        raise ReplyError("This comment already has a reply.")
    reply = Reply.objects.select_related("comment", "page__connection").get(pk=reply.pk)
    comment, page = reply.comment, reply.page

    if comment.is_deleted:
        _fail(reply, "That comment was deleted on Facebook.")
    if reply.source == Reply.Source.AUTO and page.automation_mode != FacebookPage.AutomationMode.AUTO:
        # Mode was switched off/to draft while this was queued: fall back to a draft.
        Reply.objects.filter(pk=reply.pk).update(status=Reply.Status.DRAFT, source=Reply.Source.APPROVED)
        Comment.objects.filter(pk=comment.pk).update(status=Comment.Status.DRAFTED)
        raise ReplyError("Automatic replies were switched off for this Page.")
    if not page.is_connected or not page.access_token:
        _fail(reply, "This Page isn't connected any more. Reconnect Facebook and try again.")
    if REPLY_PERMISSION not in (page.connection.granted_scopes or "").split(","):
        _fail(reply, "Replytic doesn't have permission to reply yet. Reconnect Facebook and approve replying.")

    try:
        result = graph.post(f"{comment.facebook_comment_id}/comments", message=reply.message, access_token=page.access_token)
    except graph.GraphAPIError as error:
        if error.is_auth_error:
            FacebookConnection.objects.filter(pk=page.connection_id).update(status=FacebookConnection.Status.NEEDS_RECONNECT)
            _fail(reply, "Facebook access needs renewing. Reconnect Facebook and try again.")
        _fail(reply, f"Facebook couldn't post the reply: {error}")

    reply.status = Reply.Status.SENT
    reply.facebook_reply_id = str(result.get("id", ""))
    reply.sent_at = timezone.now()
    reply.save(update_fields=["status", "facebook_reply_id", "sent_at", "updated_at"])
    Comment.objects.filter(pk=comment.pk).update(status=Comment.Status.REPLIED, updated_at=timezone.now())
    return reply


def _clean_message(message):
    message = (message or "").strip()
    if not message:
        raise ReplyError("Write a reply first.")
    if len(message) > settings.MAX_REPLY_LENGTH:
        raise ReplyError(f"Keep replies under {settings.MAX_REPLY_LENGTH} characters.")
    return message


def reply_as_person(comment, *, message, user):
    """A team member sends (or edits then sends) a reply. Returns the sent Reply."""
    message = _clean_message(message)
    if comment.is_from_page:
        raise ReplyError("You can't reply to your own Page's comment here.")
    live = Reply.objects.filter(comment=comment, status__in=Reply.LIVE_STATUSES).first()
    if live is not None and live.status != Reply.Status.DRAFT:
        raise ReplyError("This comment already has a reply.")
    if live is not None:  # edit the suggested draft, then send it
        live.message = message
        live.created_by = user
        if live.source == Reply.Source.AUTO:
            live.source = Reply.Source.APPROVED  # a person approved it, so it isn't "automatic"
        live.save(update_fields=["message", "created_by", "source", "updated_at"])
        reply = live
    else:
        try:
            with transaction.atomic():
                reply = Reply.objects.create(
                    organization=comment.organization, comment=comment, page=comment.page,
                    message=message, source=Reply.Source.MANUAL, created_by=user,
                )
        except IntegrityError:
            raise ReplyError("This comment already has a reply.") from None
    Comment.objects.filter(pk=comment.pk).update(handled_by=user)
    return send_reply(reply)


def retry_failed_reply(comment, *, user):
    failed = comment.replies.filter(status=Reply.Status.FAILED).order_by("-created_at").first()
    if failed is None:
        raise ReplyError("There's no failed reply to retry.")
    if comment.replies.filter(status__in=Reply.LIVE_STATUSES).exists():
        raise ReplyError("This comment already has a reply.")
    if failed.source == Reply.Source.AUTO:
        failed.source = Reply.Source.APPROVED
    failed.status = Reply.Status.DRAFT
    failed.created_by = user
    try:
        with transaction.atomic():
            failed.save(update_fields=["status", "source", "created_by", "updated_at"])
    except IntegrityError:
        raise ReplyError("This comment already has a reply.") from None
    return send_reply(failed)


def ignore_comment(comment, *, user):
    with transaction.atomic():
        comment.replies.filter(status=Reply.Status.DRAFT).update(status=Reply.Status.DISCARDED)
        if comment.status != Comment.Status.REPLIED:
            comment.status = Comment.Status.IGNORED
        comment.handled_by = user
        comment.save(update_fields=["status", "handled_by", "updated_at"])


def reopen_comment(comment):
    if comment.status == Comment.Status.IGNORED:
        comment.status = Comment.Status.NEW
        comment.save(update_fields=["status", "updated_at"])


def discard_draft(comment, *, user):
    updated = comment.replies.filter(status=Reply.Status.DRAFT).update(status=Reply.Status.DISCARDED)
    if updated:
        comment.status = Comment.Status.NEW
        comment.handled_by = user
        comment.save(update_fields=["status", "handled_by", "updated_at"])


def recover_stuck_replies():
    """A crash mid-send would leave a reply SENDING forever, blocking a retry. Fail it visibly instead."""
    cutoff = timezone.now() - STUCK_SENDING_AFTER
    stuck = Reply.objects.filter(status=Reply.Status.SENDING, sending_started_at__lt=cutoff)
    count = 0
    for reply in stuck:
        reply.status = Reply.Status.FAILED
        reply.error = "Sending was interrupted. Check Facebook before retrying."
        reply.save(update_fields=["status", "error", "updated_at"])
        Comment.objects.filter(pk=reply.comment_id).update(status=Comment.Status.FAILED)
        count += 1
    return count

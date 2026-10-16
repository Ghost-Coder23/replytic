import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Comment(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        DRAFTED = "DRAFTED", "Reply ready"
        FLAGGED = "FLAGGED", "Flagged"
        REPLIED = "REPLIED", "Replied"
        FAILED = "FAILED", "Reply failed"
        IGNORED = "IGNORED", "Ignored"

    NEEDS_ATTENTION = (Status.NEW, Status.DRAFTED, Status.FLAGGED, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="comments")
    page = models.ForeignKey("facebook.FacebookPage", on_delete=models.CASCADE, related_name="comments")
    # Facebook's own id makes ingestion idempotent: re-delivered webhooks never duplicate comments.
    facebook_comment_id = models.CharField(max_length=64, unique=True)
    post_id = models.CharField(max_length=64, blank=True)
    parent_id = models.CharField(max_length=64, blank=True)
    author_id = models.CharField(max_length=64, blank=True)
    author_name = models.CharField(max_length=200, blank=True)
    message = models.TextField(blank=True)
    commented_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    is_from_page = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    matched_rule = models.ForeignKey("automations.AutomationRule", null=True, blank=True, on_delete=models.SET_NULL, related_name="comments")
    decision_note = models.CharField(max_length=300, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    received_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-commented_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-commented_at"]),
            models.Index(fields=["organization", "-commented_at"]),
        ]

    def __str__(self):
        return f"{self.author_name or 'Someone'}: {self.message[:40]}"

    @property
    def live_reply(self):
        """The draft / in-flight / sent reply, if any (at most one exists)."""
        for reply in self.replies.all():
            if reply.status in Reply.LIVE_STATUSES:
                return reply
        return None

    @property
    def failed_reply(self):
        failed = [r for r in self.replies.all() if r.status == Reply.Status.FAILED]
        return failed[-1] if failed else None


class Reply(models.Model):
    class Source(models.TextChoices):
        AUTO = "AUTO", "Automatic"
        APPROVED = "APPROVED", "Approved suggestion"
        MANUAL = "MANUAL", "Written by a person"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        DISCARDED = "DISCARDED", "Discarded"

    LIVE_STATUSES = (Status.DRAFT, Status.SENDING, Status.SENT)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="replies")
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="replies")
    page = models.ForeignKey("facebook.FacebookPage", on_delete=models.CASCADE, related_name="replies")
    message = models.TextField()
    source = models.CharField(max_length=10, choices=Source.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    rule = models.ForeignKey("automations.AutomationRule", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    facebook_reply_id = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=500, blank=True)
    sending_started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # Duplicate prevention enforced by the database: a comment can have only ONE
            # draft / in-flight / sent reply, so double clicks and racing workers can't double-reply.
            models.UniqueConstraint(
                fields=["comment"], condition=Q(status__in=["DRAFT", "SENDING", "SENT"]), name="one_live_reply_per_comment",
            ),
        ]
        indexes = [models.Index(fields=["organization", "source", "status", "-sent_at"]), models.Index(fields=["page", "source", "status", "sent_at"])]

    def __str__(self):
        return f"{self.get_status_display()} reply to {self.comment_id}"

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from .crypto import EncryptedTextField


class FacebookConnection(models.Model):
    """A Facebook user who authorised Replytic on behalf of one organization."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        NEEDS_RECONNECT = "NEEDS_RECONNECT", "Needs reconnect"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="facebook_connections")
    connected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    facebook_user_id = models.CharField(max_length=64)
    facebook_user_name = models.CharField(max_length=200, blank=True)
    access_token = EncryptedTextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    granted_scopes = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "facebook_user_id"], name="unique_fb_user_per_organization"),
        ]

    def __str__(self):
        return f"{self.facebook_user_name or self.facebook_user_id} → {self.organization}"


class FacebookPage(models.Model):
    class AutomationMode(models.TextChoices):
        OFF = "OFF", "Off"
        DRAFT = "DRAFT", "Suggest replies"
        AUTO = "AUTO", "Reply automatically"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        CONNECTED = "CONNECTED", "Connected"
        ERROR = "ERROR", "Error"
        # Facebook account removed: token erased, row kept so past comments keep their Page.
        DISCONNECTED = "DISCONNECTED", "Disconnected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="facebook_pages")
    # SET_NULL, never CASCADE: removing a Facebook account must not delete the Page row,
    # because comments, replies and rules reference it and are the business's history.
    connection = models.ForeignKey(FacebookConnection, null=True, blank=True, on_delete=models.SET_NULL, related_name="pages")
    page_id = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=200, blank=True)
    access_token = EncryptedTextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE)
    # Safety first: comments are always stored, but Replytic only *suggests* replies until
    # an owner/admin deliberately switches a Page to AUTO.
    automation_mode = models.CharField(max_length=5, choices=AutomationMode.choices, default=AutomationMode.DRAFT)
    max_auto_replies_per_hour = models.PositiveSmallIntegerField(default=20)
    last_error = models.CharField(max_length=500, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "page_id"], name="unique_page_per_organization"),
            # A Facebook Page may feed only ONE organization at a time, otherwise webhooks
            # could not be routed to a single tenant.
            models.UniqueConstraint(
                fields=["page_id"], condition=Q(status="CONNECTED"), name="unique_connected_page_globally"
            ),
        ]
        indexes = [models.Index(fields=["page_id", "status"])]

    def __str__(self):
        return f"{self.name} ({self.page_id})"

    @property
    def is_connected(self):
        return self.status == self.Status.CONNECTED


class FacebookWebhookEvent(models.Model):
    """
    A verified change notification from Meta, stored raw for the Phase 3 comment
    engine. Phase 2 only receives and stores; nothing is processed or replied to.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="facebook_webhook_events")
    page = models.ForeignKey(FacebookPage, null=True, on_delete=models.SET_NULL, related_name="webhook_events")
    facebook_page_id = models.CharField(max_length=64)
    field = models.CharField(max_length=64)
    payload = models.JSONField()
    dedupe_key = models.CharField(max_length=64, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["organization", "-received_at"])]

    def __str__(self):
        return f"{self.field} for page {self.facebook_page_id}"


class FacebookDeletionRequest(models.Model):
    """
    Record of a data-deletion request from Meta (required for app review). Stores only a hash of
    the Facebook user id, so the record itself contains no personal data.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    confirmation_code = models.CharField(max_length=64, unique=True)
    facebook_user_hash = models.CharField(max_length=64)
    connections_removed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Deletion {self.confirmation_code}"

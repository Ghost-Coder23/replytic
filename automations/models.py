import uuid

from django.conf import settings
from django.db import models


class AutomationRule(models.Model):
    """
    "If a comment contains these words, do this." Rules are evaluated in priority
    order (lowest number first) and the first match wins.
    """

    class MatchMode(models.TextChoices):
        ANY = "ANY", "Any of the keywords"
        ALL = "ALL", "All of the keywords"

    class Action(models.TextChoices):
        REPLY = "REPLY", "Reply to the comment"
        FLAG = "FLAG", "Flag for a person"
        IGNORE = "IGNORE", "Ignore the comment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="automation_rules")
    page = models.ForeignKey(
        "facebook.FacebookPage", null=True, blank=True, on_delete=models.CASCADE, related_name="automation_rules",
        help_text="Leave empty to apply to all your Pages.",
    )
    name = models.CharField(max_length=120)
    keywords = models.TextField(help_text="One keyword or phrase per line.")
    exclude_keywords = models.TextField(blank=True, help_text="If a comment contains any of these, this rule is skipped.")
    match_mode = models.CharField(max_length=3, choices=MatchMode.choices, default=MatchMode.ANY)
    action = models.CharField(max_length=6, choices=Action.choices, default=Action.REPLY)
    reply_template = models.TextField(blank=True)
    priority = models.PositiveIntegerField(default=100, help_text="Lower numbers are checked first.")
    is_active = models.BooleanField(default=True)
    match_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "created_at"]
        indexes = [models.Index(fields=["organization", "is_active", "priority"])]

    def __str__(self):
        return self.name

    @staticmethod
    def _lines(value):
        return [line.strip() for line in (value or "").splitlines() if line.strip()]

    @property
    def keyword_list(self):
        return self._lines(self.keywords)

    @property
    def exclude_list(self):
        return self._lines(self.exclude_keywords)

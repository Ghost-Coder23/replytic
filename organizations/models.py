import uuid
from zoneinfo import available_timezones

from django.conf import settings
from django.db import models
from django.utils.text import slugify

DEFAULT_TIMEZONE = "Africa/Harare"


def timezone_choices():
    """Sorted (value, label) pairs for every IANA timezone."""
    return [(name, name.replace("_", " ")) for name in sorted(available_timezones())]


def organization_logo_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"organization_logos/{instance.pk}/logo.{extension}"


class Organization(models.Model):
    """
    One business (tenant) using Replytic.

    Every piece of organization-owned data added in later phases must carry a
    foreign key to this model and be queried through the current organization.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    logo = models.ImageField(upload_to=organization_logo_path, blank=True)
    description = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    whatsapp_number = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="OrganizationMembership",
        related_name="organizations",
    )

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["is_active", "name"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.generate_unique_slug(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_slug(cls, name):
        max_length = cls._meta.get_field("slug").max_length
        base = (slugify(name) or "business")[: max_length - 8]
        candidate, counter = base, 2
        while cls.objects.filter(slug=candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    @property
    def initials(self):
        words = [w for w in self.name.split() if w]
        return "".join(w[0] for w in words[:2]).upper() or "R"


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        ADMIN = "ADMIN", "Admin"
        AGENT = "AGENT", "Agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.AGENT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="unique_user_per_organization"
            )
        ]
        indexes = [
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["user", "organization"]),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.get_role_display()})"

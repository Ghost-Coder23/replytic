from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from organizations.models import Organization, OrganizationMembership

DEMO_EMAIL = "demo@replytic.local"
DEMO_PASSWORD = "DemoPassword123!"
DEMO_ORG = "Replytic Demo Business"


class Command(BaseCommand):
    help = "Create (or refresh) a demo user, organization and OWNER membership."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=DEMO_EMAIL,
            defaults={"email": DEMO_EMAIL, "first_name": "Demo", "last_name": "Owner"},
        )
        user.set_password(DEMO_PASSWORD)
        user.save()

        organization = Organization.objects.filter(name=DEMO_ORG, memberships__user=user).first()
        if organization is None:
            organization = Organization.objects.create(
                name=DEMO_ORG,
                description="A sample business used to explore Replytic.",
                email="hello@replytic.local",
            )
        OrganizationMembership.objects.update_or_create(
            user=user, organization=organization,
            defaults={"role": OrganizationMembership.Role.OWNER},
        )

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Email:        {DEMO_EMAIL}")
        self.stdout.write(f"  Password:     {DEMO_PASSWORD}")
        self.stdout.write(f"  Organization: {DEMO_ORG} (OWNER)")

import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from organizations.models import Organization, OrganizationMembership

from .helpers import PASSWORD, add_member, make_org_with_owner, make_user


class BusinessProfileTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Brandon Furniture", "brandon@example.com")
        self.client.force_login(self.owner)
        self.url = reverse("organizations:settings")

    def valid(self, **extra):
        data = {
            "form_name": "business", "name": "Brandon Furniture Ltd", "description": "Quality sofas",
            "phone": "+263 77 000 0000", "whatsapp_number": "+263 77 000 0001",
            "email": "hello@brandon.example", "address": "12 Samora Machel Ave", "timezone": "Africa/Harare",
        }
        data.update(extra)
        return data

    def test_owner_updates_business_profile_and_sees_success_message(self):
        response = self.client.post(self.url, self.valid(), follow=True)
        self.assertContains(response, "Business profile updated successfully.")
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Brandon Furniture Ltd")
        self.assertEqual(self.org.whatsapp_number, "+263 77 000 0001")

    def test_admin_can_update_business_profile(self):
        admin, _ = add_member(self.org, "admin@example.com", "ADMIN")
        self.client.force_login(admin)
        self.assertEqual(self.client.post(self.url, self.valid(name="By Admin")).status_code, 302)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "By Admin")

    def test_validation_errors_are_friendly(self):
        response = self.client.post(self.url, self.valid(name="", email="not-an-email", timezone="Mars/Base"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Please fix the highlighted fields.", status_code=400)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Brandon Furniture")

    def test_logo_upload(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            buffer = io.BytesIO()
            Image.new("RGB", (20, 20), "teal").save(buffer, "PNG")
            logo = SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")
            self.client.post(self.url, self.valid(logo=logo))
            self.org.refresh_from_db()
            self.assertTrue(self.org.logo.name.endswith("logo.png"))

    def test_non_image_logo_is_rejected(self):
        bad = SimpleUploadedFile("logo.png", b"not an image", content_type="image/png")
        response = self.client.post(self.url, self.valid(logo=bad))
        self.assertEqual(response.status_code, 400)
        self.org.refresh_from_db()
        self.assertFalse(self.org.logo)


class AccountProfileTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Brandon Furniture", "brandon@example.com")
        self.other = make_user("taken@example.com")
        self.client.force_login(self.user)
        self.url = reverse("organizations:settings")

    def test_user_can_update_own_details(self):
        response = self.client.post(self.url, {
            "form_name": "account", "first_name": "Bran", "last_name": "Don", "email": "New@Example.com",
        }, follow=True)
        self.assertContains(response, "Your account details were saved.")
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.email, self.user.username), ("Bran", "new@example.com", "new@example.com"))
        # can log in with the new email
        self.client.logout()
        self.assertTrue(self.client.login(username="new@example.com", password=PASSWORD))

    def test_invalid_email_rejected(self):
        response = self.client.post(self.url, {"form_name": "account", "first_name": "A", "last_name": "B", "email": "nope"})
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "brandon@example.com")

    def test_cannot_take_another_users_email(self):
        response = self.client.post(self.url, {"form_name": "account", "first_name": "A", "last_name": "B", "email": "TAKEN@example.com"})
        self.assertContains(response, "already uses this email", status_code=400)

    def test_cannot_edit_another_users_account_via_posted_id(self):
        self.client.post(self.url, {
            "form_name": "account", "first_name": "Mallory", "last_name": "X", "email": "brandon@example.com",
            "id": self.other.pk, "user_id": self.other.pk,
        })
        self.other.refresh_from_db()
        self.assertNotEqual(self.other.first_name, "Mallory")


@override_settings(META_APP_ID="", META_APP_SECRET="")
class PageTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Brandon Furniture", "brandon@example.com")
        self.client.force_login(self.owner)

    def test_landing_page(self):
        self.client.logout()
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Turn social comments into customers.")
        self.assertContains(response, reverse("accounts:register"))
        self.assertContains(response, "Coming soon")

    def test_dashboard_shows_zero_metrics_and_checklist(self):
        response = self.client.get(reverse("dashboard:index"))
        for label in ["Comments today", "Automated replies", "Potential leads", "Needs attention", "Getting started", "Recent activity", "Quick actions"]:
            self.assertContains(response, label)
        self.assertEqual([m["value"] for m in response.context["metrics"]], [0, 0, 0, 0])
        self.assertEqual(response.context["checklist"]["completed"], 2)

    def test_checklist_business_info_step_completes_when_profile_filled(self):
        self.org.description = "We sell sofas"
        self.org.phone = "+263 77 000 0000"
        self.org.save()
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.context["checklist"]["completed"], 3)

    def test_placeholder_pages_render_with_expected_copy(self):
        expectations = {
            "comments:index": "No comments yet",
            "dashboard:leads": "No leads yet",
            "automations:index": "No automations yet",
            "dashboard:knowledge": "Opening hours",
            "facebook:index": "available yet",
            "dashboard:analytics": "Analytics will become available once your social accounts are connected.",
            "dashboard:billing": "Billing integration coming soon",
            "organizations:team": "Invite Member",
            "organizations:settings": "Business profile",
        }
        for name, text in expectations.items():
            with self.subTest(page=name):
                self.assertContains(self.client.get(reverse(name)), text)

    def test_settings_tabs_all_render(self):
        for tab in ["business", "account", "security", "team", "integrations", "billing"]:
            with self.subTest(tab=tab):
                self.assertEqual(self.client.get(reverse("organizations:settings"), {"tab": tab}).status_code, 200)

    def test_404_page_is_branded(self):
        response = self.client.get("/this-does-not-exist/")
        self.assertContains(response, "find that page", status_code=404)

    def test_team_page_lists_members(self):
        add_member(self.org, "agent@example.com", "AGENT", "Agnes")
        response = self.client.get(reverse("organizations:team"))
        self.assertContains(response, "agent@example.com")
        self.assertContains(response, "Change Role")


class SeedDemoTests(TestCase):
    def test_seed_demo_is_idempotent_and_loginable(self):
        call_command("seed_demo", stdout=io.StringIO())
        call_command("seed_demo", stdout=io.StringIO())
        self.assertEqual(Organization.objects.filter(name="Replytic Demo Business").count(), 1)
        membership = OrganizationMembership.objects.get(user__email="demo@replytic.local")
        self.assertEqual(membership.role, "OWNER")
        self.assertTrue(self.client.login(username="demo@replytic.local", password="DemoPassword123!"))

    def test_no_demo_data_without_running_command(self):
        self.assertFalse(Organization.objects.exists())

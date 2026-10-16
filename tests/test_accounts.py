from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, OrganizationMembership

from .helpers import PASSWORD, make_org_with_owner

User = get_user_model()

REGISTER_DATA = {
    "first_name": "Brandon", "last_name": "Example", "email": "Brandon@Example.com",
    "business_name": "Brandon Furniture", "password1": PASSWORD, "password2": PASSWORD,
}


class RegistrationTests(TestCase):
    def test_register_page_renders(self):
        self.assertContains(self.client.get(reverse("accounts:register")), "Create account")

    def test_registration_creates_user_org_and_owner_membership_and_logs_in(self):
        response = self.client.post(reverse("accounts:register"), REGISTER_DATA, follow=True)
        self.assertRedirects(response, reverse("dashboard:index"))
        user = User.objects.get(email="brandon@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)  # hashed, not plain text
        organization = Organization.objects.get(name="Brandon Furniture")
        membership = OrganizationMembership.objects.get(user=user, organization=organization)
        self.assertEqual(membership.role, OrganizationMembership.Role.OWNER)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertContains(response, "Good")
        self.assertContains(response, "Brandon Furniture")

    def test_password_mismatch_shows_friendly_error(self):
        data = {**REGISTER_DATA, "password2": "different"}
        response = self.client.post(reverse("accounts:register"), data)
        self.assertContains(response, "The two passwords don&#x27;t match.")
        self.assertFalse(User.objects.exists())

    def test_weak_password_rejected(self):
        data = {**REGISTER_DATA, "password1": "12345678", "password2": "12345678"}
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_duplicate_email_rejected_case_insensitively(self):
        make_org_with_owner("Existing Co", "brandon@example.com")
        response = self.client.post(reverse("accounts:register"), REGISTER_DATA)
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.count(), 1)

    def test_authenticated_user_is_redirected_away_from_register(self):
        self.client.post(reverse("accounts:register"), REGISTER_DATA)
        self.assertRedirects(self.client.get(reverse("accounts:register")), reverse("dashboard:index"))


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Brandon Furniture", "brandon@example.com")

    def test_login_redirects_to_dashboard(self):
        response = self.client.post(reverse("accounts:login"), {"username": "brandon@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(reverse("accounts:login"), {"username": "BRANDON@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_bad_password_shows_error(self):
        response = self.client.post(reverse("accounts:login"), {"username": "brandon@example.com", "password": "nope"})
        self.assertContains(response, "don&#x27;t match")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_honours_safe_next_and_blocks_external_next(self):
        url = reverse("accounts:login")
        ok = self.client.post(url, {"username": "brandon@example.com", "password": PASSWORD, "next": reverse("organizations:team")})
        self.assertRedirects(ok, reverse("organizations:team"))
        self.client.logout()
        bad = self.client.post(url, {"username": "brandon@example.com", "password": PASSWORD, "next": "https://evil.example.com/"})
        self.assertRedirects(bad, reverse("dashboard:index"))

    def test_logout_post_redirects_to_login_and_ends_session(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_get_asks_for_confirmation_and_does_not_log_out(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:logout"))
        self.assertContains(response, "Sign out of Replytic?")
        self.assertIn("_auth_user_id", self.client.session)

    def test_authenticated_user_redirected_from_login(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("accounts:login")), reverse("dashboard:index"))


class AnonymousAccessTests(TestCase):
    PROTECTED = [
        "dashboard:index", "comments:index", "dashboard:leads", "automations:index",
        "dashboard:knowledge", "facebook:index", "dashboard:analytics", "dashboard:billing",
        "organizations:team", "organizations:settings", "organizations:create", "accounts:password_change",
    ]

    def test_every_protected_page_redirects_to_login(self):
        for name in self.PROTECTED:
            with self.subTest(page=name):
                url = reverse(name)
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('accounts:login')}?next={url}")

    def test_dashboard_path_is_login_redirected(self):
        self.assertRedirects(self.client.get("/dashboard/"), "/login/?next=/dashboard/")

    def test_public_pages_are_open(self):
        for name in ["core:home", "accounts:login", "accounts:register", "accounts:password_reset"]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_post_only_endpoints_reject_anonymous(self):
        response = self.client.post(reverse("organizations:switch"), {"organization_id": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])


class PasswordChangeTests(TestCase):
    def setUp(self):
        _, self.user, _ = make_org_with_owner("Brandon Furniture", "brandon@example.com")
        self.client.force_login(self.user)

    def test_password_change_success_keeps_user_logged_in(self):
        new = "An0ther-Str0ng-Pass!"
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": PASSWORD, "new_password1": new, "new_password2": new,
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new))
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)

    def test_wrong_old_password_fails(self):
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "wrong", "new_password1": "An0ther-Str0ng-Pass!", "new_password2": "An0ther-Str0ng-Pass!",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_password_reset_flow_sends_email(self):
        from django.core import mail
        self.client.logout()
        self.client.post(reverse("accounts:password_reset"), {"email": "brandon@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password/reset/", mail.outbox[0].body)

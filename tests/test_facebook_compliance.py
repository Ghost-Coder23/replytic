import base64
import hashlib
import hmac
import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from facebook.models import FacebookConnection, FacebookDeletionRequest, FacebookPage

from .helpers import make_org_with_owner

SECRET = "app-secret-value"


def signed_request(payload, secret=SECRET):
    def enc(raw):
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    body = enc(json.dumps({"algorithm": "HMAC-SHA256", **payload}).encode())
    sig = enc(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{sig}.{body}"


@override_settings(META_APP_SECRET=SECRET, SITE_URL="https://replytic.example.com")
class DeauthorizeAndDeletionTests(TestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Alpha", "a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Beta", "b@example.com")
        self.conn_a = self.connect(self.org_a, "fb-777", "111")
        self.conn_b = self.connect(self.org_b, "fb-777", "222")    # same Facebook person, another business
        self.other = self.connect(self.org_b, "fb-888", "333")     # a different Facebook person
        self.anon = Client(enforce_csrf_checks=True)

    def connect(self, org, fb_user, page_id):
        conn = FacebookConnection.objects.create(organization=org, facebook_user_id=fb_user, access_token="tok")
        FacebookPage.objects.create(organization=org, connection=conn, page_id=page_id, name="P" + page_id, access_token="ptok", status="CONNECTED")
        return conn

    def test_deauthorize_removes_that_users_connections_everywhere_but_no_one_elses(self):
        response = self.anon.post(reverse("facebook:deauthorize"), {"signed_request": signed_request({"user_id": "fb-777"})})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FacebookConnection.objects.filter(facebook_user_id="fb-777").exists())
        # Page rows survive (comment history!) but lose their token, link and watching status.
        for page in FacebookPage.objects.filter(page_id__in=["111", "222"]):
            self.assertEqual((page.status, page.access_token, page.connection_id), ("DISCONNECTED", "", None))
        self.assertTrue(FacebookConnection.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(FacebookPage.objects.get(page_id="333").status, "CONNECTED")

    def test_deauthorize_makes_no_network_calls(self):
        from unittest import mock
        with mock.patch("facebook.graph._open", side_effect=AssertionError("network used")):
            self.anon.post(reverse("facebook:deauthorize"), {"signed_request": signed_request({"user_id": "fb-777"})})

    def test_invalid_or_missing_signatures_are_rejected_and_delete_nothing(self):
        bad = [signed_request({"user_id": "fb-777"}, secret="wrong"), "garbage", "a.b", "", signed_request({"nouser": 1})]
        tampered_sig, tampered_body = signed_request({"user_id": "fb-888"}).split(".")
        bad.append(f"{tampered_sig}.{tampered_body}A")
        for url in ("facebook:deauthorize", "facebook:data_deletion"):
            for value in bad:
                with self.subTest(url=url, value=value[:12]):
                    self.assertEqual(self.anon.post(reverse(url), {"signed_request": value}).status_code, 400)
        self.assertEqual(FacebookConnection.objects.count(), 3)
        self.assertFalse(FacebookDeletionRequest.objects.exists())

    @override_settings(META_APP_SECRET="")
    def test_fails_closed_without_app_secret(self):
        response = self.anon.post(reverse("facebook:deauthorize"), {"signed_request": signed_request({"user_id": "fb-777"}, secret="")})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(FacebookConnection.objects.count(), 3)

    def test_get_is_not_allowed(self):
        for name in ("facebook:deauthorize", "facebook:data_deletion"):
            self.assertEqual(self.anon.get(reverse(name)).status_code, 405)

    def test_data_deletion_returns_status_url_and_code_and_deletes(self):
        response = self.anon.post(reverse("facebook:data_deletion"), {"signed_request": signed_request({"user_id": "fb-777"})})
        data = response.json()
        record = FacebookDeletionRequest.objects.get()
        self.assertEqual(data["confirmation_code"], record.confirmation_code)
        self.assertEqual(data["url"], f"https://replytic.example.com/facebook/data-deletion/status/?code={record.confirmation_code}")
        self.assertEqual(record.connections_removed, 2)
        self.assertNotIn("fb-777", record.facebook_user_hash)  # only a hash is kept
        self.assertFalse(FacebookConnection.objects.filter(facebook_user_id="fb-777").exists())

    def test_status_page_is_public_and_shows_the_confirmation(self):
        record = FacebookDeletionRequest.objects.create(confirmation_code="abc123", facebook_user_hash="x", connections_removed=1, completed_at="2026-09-19T10:00:00Z")
        page = self.anon.get(reverse("facebook:deletion_status"), {"code": "abc123"})
        self.assertContains(page, "Your data was deleted")
        self.assertContains(page, "abc123")
        self.assertContains(self.anon.get(reverse("facebook:deletion_status"), {"code": "nope"}), "Request not found")
        self.assertContains(self.anon.get(reverse("facebook:deletion_status")), "Request not found")

    def test_setup_card_lists_the_urls_for_staff(self):
        self.owner_a.is_staff = True
        self.owner_a.save()
        self.client.force_login(self.owner_a)
        page = self.client.get(reverse("facebook:index"))
        self.assertContains(page, "https://replytic.example.com/facebook/deauthorize/")
        self.assertContains(page, "https://replytic.example.com/facebook/data-deletion/")


class TunnelSettingsTests(TestCase):
    def test_site_url_host_and_origin_are_trusted(self):
        from django.conf import settings
        # settings derive these from SITE_URL at startup; default is localhost
        self.assertIn("localhost", settings.ALLOWED_HOSTS)
        self.assertTrue(all(o.startswith("http") for o in settings.CSRF_TRUSTED_ORIGINS))

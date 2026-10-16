import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from facebook.models import FacebookConnection, FacebookPage
from .helpers import make_org_with_owner
from .phase3 import make_event, make_page, comment_change


def run():
    out = io.StringIO()
    call_command("facebook_diagnose", stdout=out)
    return out.getvalue()


class ConfigurationChecksTests(TestCase):
    @override_settings(META_APP_ID="", META_APP_SECRET="", META_WEBHOOK_VERIFY_TOKEN="", SITE_URL="http://localhost:8000")
    def test_missing_config_is_flagged(self):
        output = run()
        self.assertIn("FAIL", output)
        self.assertIn("META_APP_ID", output)

    @override_settings(META_APP_ID="x", META_APP_SECRET="y", META_WEBHOOK_VERIFY_TOKEN="z", SITE_URL="https://example.ngrok-free.dev")
    def test_complete_config_passes(self):
        output = run()
        self.assertNotIn("FAIL  META", output)
        self.assertIn("https://example.ngrok-free.dev/webhooks/facebook/", output)


class ConnectionAndPageChecksTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")

    def test_no_connections_is_reported_plainly(self):
        self.assertIn("none yet", run())

    def test_healthy_connection_and_page_pass(self):
        make_page(self.org, self.owner, "111", "Alpha Page")
        output = run()
        self.assertIn("OK  Alpha Furniture: Owner", output)
        self.assertIn("OK  Alpha Furniture: Alpha Page", output)

    def test_needs_reconnect_status_is_flagged(self):
        make_page(self.org, self.owner, "111", "Alpha Page")
        FacebookConnection.objects.update(status=FacebookConnection.Status.NEEDS_RECONNECT)
        self.assertIn("FAIL", run())

    def test_undecryptable_token_is_detected_even_though_status_still_says_connected(self):
        """Regression: a Page can show CONNECTED while its token is silently unreadable."""
        page = make_page(self.org, self.owner, "111", "Alpha Page")
        with override_settings(TOKEN_ENCRYPTION_KEY="a-different-key-than-was-used-to-encrypt"):
            output = run()
        self.assertIn("FAIL", output)
        self.assertIn("can't be decrypted", output)
        # And the Page's own status is untouched -- this command never writes anything.
        page.refresh_from_db()
        self.assertEqual(page.status, FacebookPage.Status.CONNECTED)

    def test_page_with_recorded_error_is_flagged(self):
        page = make_page(self.org, self.owner, "111", "Alpha Page")
        page.last_error = "Something went wrong"
        page.save()
        self.assertIn("Something went wrong", run())

    def test_disconnected_pages_are_listed_but_not_treated_as_failures(self):
        page = make_page(self.org, self.owner, "111", "Alpha Page")
        page.status = FacebookPage.Status.DISCONNECTED
        page.access_token = ""
        page.save()
        output = run()
        self.assertIn("Alpha Page", output)
        self.assertNotIn("FAIL  Alpha Furniture: Alpha Page", output)


class WebhookEventChecksTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")
        self.page = make_page(self.org, self.owner, "111", "Alpha Page")

    def test_zero_events_is_flagged_with_next_steps(self):
        output = run()
        self.assertIn("FAIL", output)
        self.assertIn("Zero events", output)
        self.assertIn("send_test_webhook", output)

    def test_received_events_are_reported(self):
        make_event(self.page, comment_change())
        output = run()
        self.assertIn("1 event(s) received", output)

    def test_unprocessed_backlog_is_flagged(self):
        make_event(self.page, comment_change())
        output = run()
        self.assertIn("1 event(s) are still waiting", output)
        self.assertIn("process_comments", output)


class SendTestWebhookTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")

    def call(self, **kwargs):
        out = io.StringIO()
        call_command("send_test_webhook", stdout=out, **kwargs)
        return out.getvalue()

    @override_settings(META_APP_SECRET="")
    def test_requires_app_secret(self):
        with self.assertRaises(Exception):
            self.call()

    def test_requires_a_watched_page(self):
        with self.assertRaises(Exception), override_settings(META_APP_SECRET="shh"):
            self.call()

    @override_settings(META_APP_SECRET="shh")
    def test_sends_a_correctly_signed_request_to_the_given_url(self):
        make_page(self.org, self.owner, "111", "Alpha Page")
        captured = {}

        class FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(request, timeout=None):
            captured["body"] = request.data
            captured["sig"] = request.get_header("X-hub-signature-256")
            captured["url"] = request.full_url
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            output = self.call(message="How much?", url="http://testserver/webhooks/facebook/")

        self.assertIn("Server answered 200", output)
        self.assertIn("fake, so approving", output)
        from facebook import services
        self.assertTrue(services.verify_signature(captured["body"], captured["sig"]))

    @override_settings(META_APP_SECRET="shh")
    def test_unreachable_server_gives_a_clear_error(self):
        make_page(self.org, self.owner, "111", "Alpha Page")
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(Exception) as ctx:
                self.call()
        self.assertIn("runserver", str(ctx.exception))

    @override_settings(META_APP_SECRET="wrong")
    def test_server_rejecting_signature_gives_a_clear_error(self):
        make_page(self.org, self.owner, "111", "Alpha Page")
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 403, "f", {}, None)):
            with self.assertRaises(Exception) as ctx:
                self.call()
        self.assertIn("403", str(ctx.exception))

import hashlib
import hmac
import json
import time
import urllib.parse
from unittest import mock

from django.db import connection as db_connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from facebook import crypto, graph, services
from facebook.models import FacebookConnection, FacebookPage, FacebookWebhookEvent
from organizations.models import OrganizationMembership

from .helpers import add_member, make_org_with_owner

Role = OrganizationMembership.Role

META = dict(
    SITE_URL="https://replytic.example.com",
    META_APP_ID="1234567890",
    META_APP_SECRET="app-secret-value",
    META_WEBHOOK_VERIFY_TOKEN="verify-me",
    META_GRAPH_VERSION="v23.0",
    META_LOGIN_CONFIG_ID="",
    META_SCOPES=["pages_show_list", "pages_manage_metadata"],
)


class FakeGraph:
    """Stands in for facebook.graph._open. Records calls and answers like Meta would."""

    def __init__(self, permissions=("pages_show_list", "pages_manage_metadata"), pages=None, fail_on=None):
        self.calls = []
        self.permissions = permissions
        self.pages = pages if pages is not None else [
            {"id": "111", "name": "Alpha Furniture Page", "category": "Furniture", "access_token": "PAGE-TOKEN-111"},
            {"id": "222", "name": "Alpha Outlet", "category": "Shopping", "access_token": "PAGE-TOKEN-222"},
        ]
        self.fail_on = fail_on

    def __call__(self, url, *, data=None, method="GET"):
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.split("/", 2)[2]  # strip /v23.0/
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((method, path, query, data))
        if self.fail_on and self.fail_on in path:
            raise graph.GraphAPIError("Boom", code=190, status=400)
        if path == "oauth/access_token":
            if query.get("grant_type") == "fb_exchange_token":
                return {"access_token": "LONG-USER-TOKEN", "expires_in": 5184000}
            return {"access_token": "SHORT-USER-TOKEN"}
        if path == "me":
            return {"id": "fb-user-1", "name": "Brandon Example"}
        if path == "me/permissions":
            return {"data": [{"permission": p, "status": "granted"} for p in self.permissions]}
        if path == "me/accounts":
            return {"data": self.pages}
        if path.endswith("/subscribed_apps"):
            return {"success": True}
        raise AssertionError(f"Unexpected Graph call: {method} {path}")

    def paths(self):
        return [(m, p) for m, p, _, _ in self.calls]


@override_settings(**META)
class FacebookBase(TestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Alpha Furniture", "owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Beta Phones", "owner-b@example.com")
        self.agent_a, _ = add_member(self.org_a, "agent-a@example.com", Role.AGENT)
        self.client.force_login(self.owner_a)

    def make_connection(self, org, user, fb_user="fb-1", pages=(("111", "Page One"),)):
        conn = FacebookConnection.objects.create(
            organization=org, connected_by=user, facebook_user_id=fb_user,
            facebook_user_name="Someone", access_token="USER-TOKEN-" + fb_user,
        )
        made = [
            FacebookPage.objects.create(
                organization=org, connection=conn, page_id=pid, name=name, access_token=f"PAGE-TOKEN-{pid}",
            ) for pid, name in pages
        ]
        return conn, made

    def do_oauth_start(self):
        response = self.client.post(reverse("facebook:connect"))
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(response["Location"]).query))
        return response, query


class AccessTests(FacebookBase):
    def test_agent_cannot_open_facebook_pages_or_actions(self):
        self.client.force_login(self.agent_a)
        self.assertEqual(self.client.get(reverse("facebook:index")).status_code, 403)
        self.assertEqual(self.client.post(reverse("facebook:connect")).status_code, 403)
        self.assertEqual(self.client.get(reverse("facebook:callback")).status_code, 403)

    def test_anonymous_is_redirected(self):
        self.client.logout()
        for name in ("facebook:index", "facebook:callback"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_connect_requires_post(self):
        self.assertEqual(self.client.get(reverse("facebook:connect")).status_code, 405)

    @override_settings(META_APP_ID="", META_APP_SECRET="")
    def test_unconfigured_shows_setup_and_never_redirects_to_facebook(self):
        page = self.client.get(reverse("facebook:index"))
        self.assertContains(page, "available yet")
        self.assertNotContains(page, "META_APP_ID")  # customers are never told to edit server settings
        self.owner_a.is_staff = True
        self.owner_a.save()
        self.assertContains(self.client.get(reverse("facebook:index")), "Finish setup")
        response = self.client.post(reverse("facebook:connect"), follow=True)
        self.assertContains(response, "isn&#x27;t set up")
        self.assertEqual(response.redirect_chain[-1][0], reverse("facebook:index"))


class OAuthTests(FacebookBase):
    def test_connect_redirects_to_meta_with_state_and_least_privilege_scopes(self):
        response, query = self.do_oauth_start()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://www.facebook.com/v23.0/dialog/oauth?"))
        self.assertEqual(query["client_id"], "1234567890")
        self.assertEqual(query["redirect_uri"], "https://replytic.example.com/facebook/callback/")
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["scope"], "pages_show_list,pages_manage_metadata")
        self.assertGreaterEqual(len(query["state"]), 32)
        self.assertEqual(self.client.session[services.OAUTH_SESSION_KEY]["state"], query["state"])

    @override_settings(META_LOGIN_CONFIG_ID="cfg-42")
    def test_login_for_business_uses_config_id_instead_of_scope(self):
        _, query = self.do_oauth_start()
        self.assertEqual(query["config_id"], "cfg-42")
        self.assertNotIn("scope", query)

    def test_successful_callback_stores_encrypted_connection_and_lists_pages(self):
        _, query = self.do_oauth_start()
        fake = FakeGraph()
        with mock.patch("facebook.graph._open", fake):
            response = self.client.get(reverse("facebook:callback"), {"code": "AUTHCODE", "state": query["state"]}, follow=True)
        self.assertContains(response, "Connected as Brandon Example")
        conn = FacebookConnection.objects.get(organization=self.org_a)
        self.assertEqual(conn.facebook_user_id, "fb-user-1")
        self.assertEqual(conn.access_token, "LONG-USER-TOKEN")
        self.assertIsNotNone(conn.token_expires_at)
        self.assertEqual(conn.connected_by, self.owner_a)
        self.assertEqual(FacebookPage.objects.filter(organization=self.org_a).count(), 2)
        self.assertContains(response, "Alpha Furniture Page")
        # code exchange used our redirect uri and secret; secret never reaches the page
        exchange = fake.calls[0]
        self.assertEqual(exchange[2]["code"], "AUTHCODE")
        self.assertEqual(exchange[2]["redirect_uri"], "https://replytic.example.com/facebook/callback/")
        self.assertNotContains(response, "app-secret-value")
        self.assertNotContains(response, "LONG-USER-TOKEN")
        self.assertNotContains(response, "PAGE-TOKEN-111")

    def test_tokens_are_encrypted_in_the_database(self):
        _, query = self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph()):
            self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]})
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT access_token FROM facebook_facebookconnection")
            raw_user = cursor.fetchone()[0]
            cursor.execute("SELECT access_token FROM facebook_facebookpage")
            raw_pages = [row[0] for row in cursor.fetchall()]
        for raw in [raw_user, *raw_pages]:
            self.assertTrue(raw)
            self.assertNotIn("TOKEN", raw)

    def test_wrong_state_is_rejected(self):
        self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph()) as fake:
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": "forged"}, follow=True)
        self.assertContains(response, "couldn&#x27;t verify")
        self.assertFalse(FacebookConnection.objects.exists())
        self.assertEqual(fake.calls, [])  # no token exchange attempted

    def test_missing_state_or_no_flow_started_is_rejected(self):
        with mock.patch("facebook.graph._open", FakeGraph()):
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": "x"}, follow=True)
        self.assertContains(response, "expired")
        self.assertFalse(FacebookConnection.objects.exists())

    def test_state_is_single_use(self):
        _, query = self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph()):
            self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]})
            replay = self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]}, follow=True)
        self.assertContains(replay, "expired")

    def test_expired_state_is_rejected(self):
        _, query = self.do_oauth_start()
        session = self.client.session
        session[services.OAUTH_SESSION_KEY]["ts"] = int(time.time()) - 3600
        session.save()
        with mock.patch("facebook.graph._open", FakeGraph()):
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]}, follow=True)
        self.assertContains(response, "took too long")
        self.assertFalse(FacebookConnection.objects.exists())

    def test_state_cannot_be_completed_in_a_different_business(self):
        _, query = self.do_oauth_start()
        other, _ = add_member(self.org_a, "admin-a@example.com", Role.ADMIN)
        # Same user switches to another business mid-flow.
        from organizations.services import create_organization
        org_c, _ = create_organization(owner=self.owner_a, name="Gamma Hardware")
        self.client.post(reverse("organizations:switch"), {"organization_id": str(org_c.pk)})
        with mock.patch("facebook.graph._open", FakeGraph()):
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]}, follow=True)
        self.assertContains(response, "switched business")
        self.assertFalse(FacebookConnection.objects.exists())

    def test_state_cannot_be_used_by_another_user(self):
        _, query = self.do_oauth_start()
        stolen_state = query["state"]
        attacker = Client()
        attacker.force_login(self.owner_b)
        with mock.patch("facebook.graph._open", FakeGraph()):
            response = attacker.get(reverse("facebook:callback"), {"code": "c", "state": stolen_state}, follow=True)
        self.assertContains(response, "expired")
        self.assertFalse(FacebookConnection.objects.exists())

    def test_missing_permissions_are_rejected(self):
        _, query = self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph(permissions=("pages_show_list",))):
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]}, follow=True)
        self.assertContains(response, "approve every requested permission")
        self.assertFalse(FacebookConnection.objects.exists())

    def test_user_cancelling_at_facebook_is_handled(self):
        self.do_oauth_start()
        response = self.client.get(reverse("facebook:callback"), {"error": "access_denied"}, follow=True)
        self.assertContains(response, "cancelled")
        self.assertNotIn(services.OAUTH_SESSION_KEY, self.client.session)

    def test_graph_failure_during_exchange_shows_friendly_error(self):
        _, query = self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph(fail_on="oauth/access_token")):
            response = self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]}, follow=True)
        self.assertContains(response, "couldn&#x27;t complete the sign-in")
        self.assertFalse(FacebookConnection.objects.exists())


class PageManagementTests(FacebookBase):
    def setUp(self):
        super().setUp()
        self.conn, (self.page,) = self.make_connection(self.org_a, self.owner_a)

    def test_watching_a_page_subscribes_to_feed_and_marks_connected(self):
        fake = FakeGraph()
        with mock.patch("facebook.graph._open", fake):
            response = self.client.post(reverse("facebook:connect_page", args=[self.page.pk]), follow=True)
        self.assertContains(response, "now watching Page One")
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, FacebookPage.Status.CONNECTED)
        method, path, _, data = fake.calls[0]
        self.assertEqual((method, path), ("POST", "111/subscribed_apps"))
        self.assertEqual(data["subscribed_fields"], "feed")
        self.assertEqual(data["access_token"], "PAGE-TOKEN-111")

    def test_graph_error_marks_page_and_reports(self):
        with mock.patch("facebook.graph._open", FakeGraph(fail_on="subscribed_apps")):
            response = self.client.post(reverse("facebook:connect_page", args=[self.page.pk]), follow=True)
        self.assertContains(response, "wouldn&#x27;t let Replytic watch")
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, FacebookPage.Status.ERROR)

    def test_page_cannot_be_connected_to_two_businesses(self):
        _, (other_page,) = self.make_connection(self.org_b, self.owner_b, fb_user="fb-2", pages=(("111", "Same Page"),))
        other_page.status = FacebookPage.Status.CONNECTED
        other_page.save()
        with mock.patch("facebook.graph._open", FakeGraph()) as fake:
            response = self.client.post(reverse("facebook:connect_page", args=[self.page.pk]), follow=True)
        self.assertContains(response, "already connected to another business")
        self.assertEqual(fake.calls, [])
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, FacebookPage.Status.AVAILABLE)

    def test_database_enforces_one_connected_business_per_page(self):
        from django.db import IntegrityError, transaction
        _, (other,) = self.make_connection(self.org_b, self.owner_b, fb_user="fb-2", pages=(("111", "Same"),))
        self.page.status = other.status = FacebookPage.Status.CONNECTED
        self.page.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            other.save()

    @override_settings(META_WEBHOOK_VERIFY_TOKEN="")
    def test_cannot_watch_without_webhook_settings(self):
        with mock.patch("facebook.graph._open", FakeGraph()) as fake:
            response = self.client.post(reverse("facebook:connect_page", args=[self.page.pk]), follow=True)
        self.assertContains(response, "Webhooks aren&#x27;t configured")
        self.assertEqual(fake.calls, [])

    def test_stop_watching_unsubscribes_and_resets(self):
        self.page.status = FacebookPage.Status.CONNECTED
        self.page.save()
        fake = FakeGraph()
        with mock.patch("facebook.graph._open", fake):
            self.client.post(reverse("facebook:disconnect_page", args=[self.page.pk]))
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, FacebookPage.Status.AVAILABLE)
        self.assertEqual(fake.calls[0][:2], ("DELETE", "111/subscribed_apps"))

    def test_disconnect_account_deletes_tokens_and_pages(self):
        self.page.status = FacebookPage.Status.CONNECTED
        self.page.save()
        with mock.patch("facebook.graph._open", FakeGraph()):
            self.client.post(reverse("facebook:disconnect_account", args=[self.conn.pk]))
        self.assertFalse(FacebookConnection.objects.exists())
        self.page.refresh_from_db()  # the row is kept for history, but the token is gone
        self.assertEqual((self.page.status, self.page.access_token, self.page.connection_id), ("DISCONNECTED", "", None))
        self.assertNotContains(self.client.get(reverse("facebook:index")), "Page One")

    def test_refresh_removes_stale_pages_and_flags_lost_connected_ones(self):
        gone, kept = FacebookPage.objects.create(
            organization=self.org_a, connection=self.conn, page_id="333", name="Gone", access_token="t"
        ), FacebookPage.objects.create(
            organization=self.org_a, connection=self.conn, page_id="444", name="Kept", access_token="t",
            status=FacebookPage.Status.CONNECTED,
        )
        fake = FakeGraph(pages=[{"id": "111", "name": "Renamed", "category": "X", "access_token": "NEW"}])
        with mock.patch("facebook.graph._open", fake):
            self.client.post(reverse("facebook:refresh_pages"))
        gone.refresh_from_db()
        self.assertEqual((gone.status, gone.access_token), ("DISCONNECTED", ""))  # not deleted: it may have comments
        kept.refresh_from_db()
        self.assertEqual(kept.status, FacebookPage.Status.ERROR)
        self.page.refresh_from_db()
        self.assertEqual((self.page.name, self.page.access_token), ("Renamed", "NEW"))

    def test_revoked_facebook_access_flags_reconnect(self):
        with mock.patch("facebook.graph._open", FakeGraph(fail_on="me/accounts")):
            response = self.client.post(reverse("facebook:refresh_pages"), follow=True)
        self.assertContains(response, "Please reconnect")
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.status, FacebookConnection.Status.NEEDS_RECONNECT)
        self.assertContains(self.client.get(reverse("facebook:index")), "Facebook access needs renewing")

    def test_dashboard_checklist_step_completes_when_a_page_is_watched(self):
        before = self.client.get(reverse("dashboard:index")).context["checklist"]["completed"]
        self.page.status = FacebookPage.Status.CONNECTED
        self.page.save()
        after = self.client.get(reverse("dashboard:index")).context["checklist"]["completed"]
        self.assertEqual(after, before + 1)


class FacebookTenantIsolationTests(FacebookBase):
    def setUp(self):
        super().setUp()
        self.conn_a, (self.page_a,) = self.make_connection(self.org_a, self.owner_a, "fb-a", (("111", "Alpha Page"),))
        self.conn_b, (self.page_b,) = self.make_connection(self.org_b, self.owner_b, "fb-b", (("999", "Beta Secret Page"),))

    def test_facebook_page_lists_only_own_pages(self):
        response = self.client.get(reverse("facebook:index"))
        self.assertContains(response, "Alpha Page")
        self.assertNotContains(response, "Beta Secret Page")

    def test_other_organizations_page_ids_return_404(self):
        with mock.patch("facebook.graph._open", FakeGraph()) as fake:
            for name in ("facebook:connect_page", "facebook:disconnect_page"):
                self.assertEqual(self.client.post(reverse(name, args=[self.page_b.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("facebook:disconnect_account", args=[self.conn_b.pk])).status_code, 404)
        self.assertEqual(fake.calls, [])
        self.page_b.refresh_from_db()
        self.assertEqual(self.page_b.status, FacebookPage.Status.AVAILABLE)
        self.assertTrue(FacebookConnection.objects.filter(pk=self.conn_b.pk).exists())

    def test_refresh_only_syncs_own_connections(self):
        fake = FakeGraph(pages=[])
        with mock.patch("facebook.graph._open", fake):
            self.client.post(reverse("facebook:refresh_pages"))
        tokens_used = [c[2].get("access_token") for c in fake.calls]
        self.assertEqual(tokens_used, ["USER-TOKEN-fb-a"])
        self.assertTrue(FacebookPage.objects.filter(pk=self.page_b.pk).exists())

    def test_isolation_is_symmetric(self):
        self.client.force_login(self.owner_b)
        self.assertEqual(self.client.post(reverse("facebook:connect_page", args=[self.page_a.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("facebook:index")), "Alpha Page")

    def test_oauth_connection_is_created_under_the_current_business_only(self):
        _, query = self.do_oauth_start()
        with mock.patch("facebook.graph._open", FakeGraph()):
            self.client.get(reverse("facebook:callback"), {"code": "c", "state": query["state"]})
        self.assertTrue(FacebookConnection.objects.filter(organization=self.org_a, facebook_user_id="fb-user-1").exists())
        self.assertFalse(FacebookConnection.objects.filter(organization=self.org_b, facebook_user_id="fb-user-1").exists())


def signed(body: bytes, secret="app-secret-value"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookTests(FacebookBase):
    def setUp(self):
        super().setUp()
        _, (self.page_a,) = self.make_connection(self.org_a, self.owner_a, "fb-a", (("111", "Alpha Page"),))
        self.page_a.status = FacebookPage.Status.CONNECTED
        self.page_a.save()
        _, (self.page_b,) = self.make_connection(self.org_b, self.owner_b, "fb-b", (("222", "Beta Page"),))
        self.page_b.status = FacebookPage.Status.CONNECTED
        self.page_b.save()
        self.url = reverse("facebook:webhook")
        self.anon = Client(enforce_csrf_checks=True)

    def payload(self, page_id="111", comment_id="c1"):
        return {"object": "page", "entry": [{
            "id": page_id, "time": 1700000000,
            "changes": [{"field": "feed", "value": {"item": "comment", "verb": "add", "comment_id": comment_id, "message": "How much?"}}],
        }]}

    def post(self, payload, signature=None, client=None):
        body = json.dumps(payload).encode()
        headers = {"HTTP_X_HUB_SIGNATURE_256": signature if signature is not None else signed(body)}
        return (client or self.anon).post(self.url, body, content_type="application/json", **headers)

    def test_verification_handshake_echoes_challenge(self):
        response = self.anon.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
        self.assertEqual((response.status_code, response.content), (200, b"12345"))

    def test_verification_rejects_wrong_token_and_mode(self):
        for params in ({"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
                       {"hub.mode": "unsubscribe", "hub.verify_token": "verify-me", "hub.challenge": "1"}, {}):
            self.assertEqual(self.anon.get(self.url, params).status_code, 403)

    @override_settings(META_WEBHOOK_VERIFY_TOKEN="")
    def test_verification_fails_closed_when_token_not_configured(self):
        self.assertEqual(self.anon.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}).status_code, 403)

    def test_valid_signed_event_is_stored_under_the_owning_business(self):
        response = self.post(self.payload("111"))
        self.assertEqual((response.status_code, response.content), (200, b"EVENT_RECEIVED"))
        event = FacebookWebhookEvent.objects.get()
        self.assertEqual(event.organization, self.org_a)
        self.assertEqual(event.page, self.page_a)
        self.assertEqual(event.field, "feed")
        self.assertEqual(event.payload["value"]["comment_id"], "c1")
        self.assertIsNone(event.processed_at)

    def test_events_are_routed_to_the_right_tenant(self):
        self.post(self.payload("222", "c9"))
        event = FacebookWebhookEvent.objects.get()
        self.assertEqual(event.organization, self.org_b)
        self.assertFalse(FacebookWebhookEvent.objects.filter(organization=self.org_a).exists())

    def test_invalid_or_missing_signature_is_rejected_and_nothing_stored(self):
        body = json.dumps(self.payload()).encode()
        for header in ("sha256=" + "0" * 64, signed(body, "wrong-secret"), "", "md5=abc"):
            with self.subTest(header=header[:12]):
                response = self.anon.post(self.url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=header)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.anon.post(self.url, body, content_type="application/json").status_code, 403)
        self.assertFalse(FacebookWebhookEvent.objects.exists())

    def test_tampered_body_fails_signature(self):
        body = json.dumps(self.payload()).encode()
        sig = signed(body)
        tampered = body.replace(b"111", b"222")
        response = self.anon.post(self.url, tampered, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
        self.assertEqual(response.status_code, 403)

    def test_redelivery_is_deduplicated(self):
        self.post(self.payload())
        self.post(self.payload())
        self.assertEqual(FacebookWebhookEvent.objects.count(), 1)
        self.post(self.payload(comment_id="c2"))
        self.assertEqual(FacebookWebhookEvent.objects.count(), 2)

    def test_unknown_or_disconnected_pages_are_ignored(self):
        self.post(self.payload("555"))
        self.page_a.status = FacebookPage.Status.AVAILABLE
        self.page_a.save()
        response = self.post(self.payload("111"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FacebookWebhookEvent.objects.exists())

    def test_non_page_objects_and_bad_json_are_handled(self):
        self.assertEqual(self.post({"object": "instagram", "entry": []}).status_code, 200)
        body = b"{not json"
        response = self.anon.post(self.url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signed(body))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(FacebookWebhookEvent.objects.exists())

    def test_webhook_needs_no_login_and_no_csrf_token(self):
        self.assertEqual(self.post(self.payload()).status_code, 200)

    def test_index_shows_received_event_count(self):
        self.post(self.payload())
        self.assertEqual(self.client.get(reverse("facebook:index")).context["event_count"], 1)


class CryptoTests(TestCase):
    def test_round_trip_and_ciphertext_differs(self):
        secret = "EAAB-super-secret"
        encrypted = crypto.encrypt(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(crypto.decrypt(encrypted), secret)

    def test_wrong_key_yields_empty_string_not_an_exception(self):
        encrypted = crypto.encrypt("value")
        from cryptography.fernet import Fernet
        with override_settings(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.assertEqual(crypto.decrypt(encrypted), "")

    def test_empty_values_stay_empty(self):
        self.assertEqual((crypto.encrypt(""), crypto.decrypt("")), ("", ""))

    def test_undecryptable_token_asks_user_to_reconnect(self):
        org, owner, _ = make_org_with_owner("Alpha", "o@example.com")
        conn = FacebookConnection.objects.create(organization=org, facebook_user_id="x", access_token="tok")
        from cryptography.fernet import Fernet
        conn = FacebookConnection.objects.get(pk=conn.pk)
        with override_settings(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            conn = FacebookConnection.objects.get(pk=conn.pk)
            with self.assertRaises(services.FacebookIntegrationError):
                services.sync_pages(conn)
        conn.refresh_from_db()
        self.assertEqual(conn.status, FacebookConnection.Status.NEEDS_RECONNECT)


class GraphClientTests(TestCase):
    def test_paging_urls_must_stay_on_graph_host(self):
        with self.assertRaises(graph.GraphAPIError):
            graph.get_url("https://evil.example.com/steal")

    @override_settings(**META)
    def test_signature_helper_rejects_when_secret_missing(self):
        with override_settings(META_APP_SECRET=""):
            self.assertFalse(services.verify_signature(b"{}", signed(b"{}", "")))

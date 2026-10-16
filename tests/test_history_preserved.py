"""Regression: removing a Facebook account must never delete comments, replies or rules."""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from automations.models import AutomationRule
from comments.models import Comment, Reply
from facebook import services
from facebook.models import FacebookPage

from .helpers import make_org_with_owner
from .phase3 import FakeReplyGraph, ingest, make_page, make_rule
from .test_facebook import FakeGraph, META


@override_settings(**META)
class HistoryPreservedTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha", "o@example.com")
        self.org.whatsapp_number = "+263 77 000 0001"
        self.org.save()
        self.page = make_page(self.org, self.owner)
        self.rule = make_rule(self.org, page=self.page, name="Page rule")
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            self.comment = ingest(self.page, message="price?", author_id="shopper-1", author_name="Shopper")
        self.client.force_login(self.owner)

    def disconnect(self):
        with mock.patch("facebook.graph._open", FakeGraph()):
            self.client.post(reverse("facebook:disconnect_account", args=[self.page.connection_id]))

    def test_disconnecting_the_account_keeps_comments_replies_and_rules(self):
        self.disconnect()
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk).exists())
        self.assertTrue(Reply.objects.filter(comment=self.comment).exists())
        self.assertTrue(AutomationRule.objects.filter(pk=self.rule.pk).exists())
        self.page.refresh_from_db()
        self.assertEqual((self.page.status, self.page.access_token), ("DISCONNECTED", ""))

    def test_history_stays_visible_in_the_inbox_after_disconnecting(self):
        self.disconnect()
        response = self.client.get(reverse("comments:index"), {"filter": "all"})
        self.assertContains(response, "price?")
        self.assertContains(response, "Alpha Page")

    def test_disconnected_page_cannot_be_used_or_offered(self):
        self.disconnect()
        self.assertNotContains(self.client.get(reverse("facebook:index")), "Alpha Page")
        self.assertNotIn(self.page, self.client.get(reverse("automations:create")).context["form"].fields["page"].queryset)

    def test_reconnecting_reuses_the_same_page_row_and_history(self):
        self.disconnect()
        response = self.client.post(reverse("facebook:connect"))
        from urllib.parse import parse_qsl, urlparse
        state = dict(parse_qsl(urlparse(response["Location"]).query))["state"]
        pages = [{"id": "111", "name": "Alpha Page", "category": "Retail", "access_token": "NEW-PAGE-TOKEN"}]
        with mock.patch("facebook.graph._open", FakeGraph(pages=pages)):
            self.client.get(reverse("facebook:callback"), {"code": "c", "state": state})
        self.page.refresh_from_db()
        self.assertEqual((self.page.status, self.page.access_token), ("AVAILABLE", "NEW-PAGE-TOKEN"))
        self.assertEqual(FacebookPage.objects.filter(page_id="111").count(), 1)
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk, page=self.page).exists())

    def test_deletion_request_erases_a_persons_own_comments_but_keeps_others(self):
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            other = ingest(self.page, message="stock?", author_id="shopper-2", author_name="Other")
        services.forget_facebook_user("shopper-1")
        self.comment.refresh_from_db()
        self.assertEqual((self.comment.message, self.comment.author_name, self.comment.author_id), ("", "", ""))
        other.refresh_from_db()
        self.assertEqual(other.message, "stock?")

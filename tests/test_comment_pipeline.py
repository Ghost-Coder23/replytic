from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from comments import services
from comments.models import Comment, Reply
from facebook import graph
from facebook.models import FacebookConnection, FacebookWebhookEvent

from .helpers import make_org_with_owner
from .phase3 import FakeReplyGraph, comment_change, ingest, make_event, make_page, make_rule


class PipelineBase(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")
        self.org.phone = "+263 77 000 0000"
        self.org.whatsapp_number = "+263 77 000 0001"
        self.org.save()
        self.page = make_page(self.org, self.owner)
        self.rule = make_rule(self.org, template="Hi {first_name}, message us on {whatsapp}!")

    def set_mode(self, mode):
        self.page.automation_mode = mode
        self.page.save()


class IngestionTests(PipelineBase):
    def test_comment_is_created_from_event_and_event_marked_processed(self):
        change = comment_change(comment_id="c-1", message="Hello there", author_name="Rudo K")
        event = make_event(self.page, change)
        self.assertTrue(services.process_event(event))
        comment = Comment.objects.get(facebook_comment_id="c-1")
        self.assertEqual((comment.organization, comment.page, comment.author_name), (self.org, self.page, "Rudo K"))
        self.assertEqual(comment.status, Comment.Status.NEW)
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at)

    def test_redelivered_comment_is_not_duplicated_or_answered_twice(self):
        self.set_mode("AUTO")
        change = comment_change(comment_id="c-dup", message="price?")
        fake = FakeReplyGraph()
        with mock.patch("facebook.graph._open", fake):
            services.process_event(make_event(self.page, change))
            services.process_event(make_event(self.page, change))
        self.assertEqual(Comment.objects.filter(facebook_comment_id="c-dup").count(), 1)
        self.assertEqual(len(fake.posts()), 1)

    def test_pages_own_comments_are_ignored_and_never_answered(self):
        self.set_mode("AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="price list attached", author_id=self.page.page_id, author_name="Alpha Page")
        self.assertEqual(comment.status, Comment.Status.IGNORED)
        self.assertTrue(comment.is_from_page)
        self.assertFalse(Reply.objects.exists())
        self.assertEqual(fake.calls, [])

    def test_missing_text_is_fetched_from_graph(self):
        fake = FakeReplyGraph(comment_details={"message": "How much?", "from": {"id": "u9", "name": "Chipo N"}, "created_time": "2026-09-19T10:00:00+0000"})
        with mock.patch("facebook.graph._open", fake):
            comment = ingest(self.page, message=None, author_id="", author_name="")
        self.assertEqual((comment.message, comment.author_name), ("How much?", "Chipo N"))
        self.assertEqual(fake.calls[0][0], "GET")

    def test_edit_updates_text_without_a_second_reply(self):
        comment = ingest(self.page, comment_id="c-e", message="hi")
        services.process_event(make_event(self.page, comment_change(comment_id="c-e", message="price now?", verb="edited")))
        comment.refresh_from_db()
        self.assertEqual(comment.message, "price now?")
        self.assertFalse(Reply.objects.exists())

    def test_deleted_comment_is_hidden(self):
        comment = ingest(self.page, comment_id="c-d", message="hi")
        services.process_event(make_event(self.page, comment_change(comment_id="c-d", verb="remove", message=None)))
        comment.refresh_from_db()
        self.assertTrue(comment.is_deleted)

    def test_non_comment_feed_events_are_ignored_but_marked_processed(self):
        event = make_event(self.page, comment_change(item="post"))
        services.process_event(event)
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at)
        self.assertFalse(Comment.objects.exists())

    def test_failing_events_are_retried_then_given_up(self):
        event = make_event(self.page, comment_change())
        with mock.patch("comments.services.ingest_event", side_effect=RuntimeError("boom")):
            for _ in range(services.MAX_EVENT_ATTEMPTS):
                self.assertFalse(services.process_event(event))
        event.refresh_from_db()
        self.assertEqual(event.attempts, services.MAX_EVENT_ATTEMPTS)
        self.assertIsNotNone(event.processed_at)
        self.assertIn("boom", event.error)

    def test_process_pending_events_can_be_limited_to_one_organization(self):
        other_org, other_owner, _ = make_org_with_owner("Beta", "b@example.com")
        other_page = make_page(other_org, other_owner, page_id="999", name="Beta Page")
        make_event(self.page, comment_change(comment_id="mine"))
        make_event(other_page, comment_change(comment_id="theirs"))
        self.assertEqual(services.process_pending_events(organization=self.org), 1)
        self.assertTrue(Comment.objects.filter(facebook_comment_id="mine").exists())
        self.assertFalse(Comment.objects.filter(facebook_comment_id="theirs").exists())
        self.assertEqual(FacebookWebhookEvent.objects.filter(processed_at__isnull=True).count(), 1)


class AutomationModeTests(PipelineBase):
    def test_default_mode_is_draft_and_never_posts(self):
        self.assertEqual(self.page.automation_mode, "DRAFT")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="How much is it?")
        self.assertEqual(comment.status, Comment.Status.DRAFTED)
        reply = comment.live_reply
        self.assertEqual((reply.status, reply.source), (Reply.Status.DRAFT, Reply.Source.APPROVED))
        self.assertEqual(reply.message, "Hi Tendai, message us on +263 77 000 0001!")
        self.assertEqual(fake.calls, [])
        self.assertEqual(comment.matched_rule, self.rule)

    def test_auto_mode_posts_the_reply_publicly(self):
        self.set_mode("AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, comment_id="c-auto", message="price please")
        method, path, _, data = fake.posts()[0]
        self.assertEqual(path, "c-auto/comments")
        self.assertEqual(data["message"], "Hi Tendai, message us on +263 77 000 0001!")
        self.assertEqual(data["access_token"], "PAGE-TOKEN-111")
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.REPLIED)
        reply = comment.live_reply
        self.assertEqual((reply.status, reply.source), (Reply.Status.SENT, Reply.Source.AUTO))
        self.assertTrue(reply.facebook_reply_id.startswith("reply-"))
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.match_count, 1)

    def test_off_mode_stores_comments_but_applies_no_rules(self):
        self.set_mode("OFF")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="price?")
        self.assertEqual(comment.status, Comment.Status.NEW)
        self.assertIsNone(comment.matched_rule)
        self.assertFalse(Reply.objects.exists())
        self.assertEqual(fake.calls, [])

    def test_no_matching_rule_leaves_comment_for_a_person(self):
        self.set_mode("AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="Lovely photo!")
        self.assertEqual(comment.status, Comment.Status.NEW)
        self.assertIn("No rule matched", comment.decision_note)
        self.assertEqual(fake.calls, [])

    def test_flag_and_ignore_rules(self):
        make_rule(self.org, name="Complaints", keywords="refund", action="FLAG", template="", priority=1)
        make_rule(self.org, name="Spam", keywords="free followers", action="IGNORE", template="", priority=2)
        self.set_mode("AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            flagged = ingest(self.page, message="I want a refund")
            ignored = ingest(self.page, message="Get FREE followers now")
        self.assertEqual(flagged.status, Comment.Status.FLAGGED)
        self.assertEqual(ignored.status, Comment.Status.IGNORED)
        self.assertEqual(fake.calls, [])

    def test_missing_placeholder_downgrades_auto_to_draft(self):
        self.org.whatsapp_number = ""
        self.org.save()
        self.set_mode("AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="price?")
        self.assertEqual(comment.status, Comment.Status.DRAFTED)
        self.assertIn("whatsapp", comment.decision_note)
        self.assertEqual(fake.calls, [])

    def test_old_comments_are_never_auto_answered(self):
        self.set_mode("AUTO")
        two_days_ago = int((timezone.now() - timedelta(days=2)).timestamp())
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            comment = ingest(self.page, message="price?", created=two_days_ago)
        self.assertEqual(comment.status, Comment.Status.DRAFTED)
        self.assertEqual(fake.calls, [])

    def test_hourly_limit_downgrades_to_draft(self):
        self.page.max_auto_replies_per_hour = 2
        self.page.automation_mode = "AUTO"
        self.page.save()
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            results = [ingest(self.page, message="price?") for _ in range(3)]
        self.assertEqual(len(fake.posts()), 2)
        self.assertEqual([c.status for c in results], ["REPLIED", "REPLIED", "DRAFTED"])
        self.assertIn("hourly", results[2].decision_note)

    def test_rules_of_other_organizations_never_apply(self):
        other_org, other_owner, _ = make_org_with_owner("Beta", "b@example.com")
        make_rule(other_org, name="Foreign", keywords="hello", template="Hijacked!")
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            comment = ingest(self.page, message="hello")
        self.assertIsNone(comment.matched_rule)


class SendingSafetyTests(PipelineBase):
    def drafted(self, message="price?"):
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            return ingest(self.page, message=message)

    def test_sending_twice_posts_once(self):
        comment = self.drafted()
        reply = comment.live_reply
        fake = FakeReplyGraph()
        with mock.patch("facebook.graph._open", fake):
            services.send_reply(reply)
            with self.assertRaises(services.ReplyError):
                services.send_reply(reply)
        self.assertEqual(len(fake.posts()), 1)

    def test_database_rejects_a_second_live_reply(self):
        comment = self.drafted()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reply.objects.create(organization=self.org, comment=comment, page=self.page, message="again", source="MANUAL")

    def test_failed_reply_does_not_block_a_new_one(self):
        comment = self.drafted()
        Reply.objects.filter(pk=comment.live_reply.pk).update(status="FAILED")
        Reply.objects.create(organization=self.org, comment=comment, page=self.page, message="second try", source="MANUAL")

    def test_switching_to_draft_mode_stops_a_queued_automatic_reply(self):
        comment = self.drafted()
        reply = comment.live_reply
        Reply.objects.filter(pk=reply.pk).update(source="AUTO")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            with self.assertRaises(services.ReplyError):
                services.send_reply(Reply.objects.get(pk=reply.pk))
        self.assertEqual(fake.calls, [])
        reply.refresh_from_db()
        self.assertEqual((reply.status, reply.source), ("DRAFT", "APPROVED"))

    def test_missing_reply_permission_fails_without_calling_facebook(self):
        FacebookConnection.objects.filter(pk=self.page.connection_id).update(granted_scopes="pages_show_list")
        comment = self.drafted()
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            with self.assertRaises(services.ReplyError):
                services.reply_as_person(comment, message="Hello", user=self.owner)
        self.assertEqual(fake.calls, [])
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.FAILED)
        self.assertIn("permission", comment.failed_reply.error)

    def test_facebook_error_marks_failed_then_retry_succeeds(self):
        comment = self.drafted()
        boom = graph.GraphAPIError("Rate limited", code=4, status=400)
        with mock.patch("facebook.graph._open", FakeReplyGraph(fail_post=boom)):
            with self.assertRaises(services.ReplyError):
                services.send_reply(comment.live_reply)
        comment = Comment.objects.get(pk=comment.pk)
        self.assertEqual(comment.status, Comment.Status.FAILED)
        self.assertIn("Rate limited", comment.failed_reply.error)
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            services.retry_failed_reply(comment, user=self.owner)
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.REPLIED)
        self.assertEqual(len(fake.posts()), 1)
        self.assertEqual(comment.live_reply.source, Reply.Source.APPROVED)

    def test_expired_facebook_access_asks_to_reconnect(self):
        comment = self.drafted()
        with mock.patch("facebook.graph._open", FakeReplyGraph(fail_post=graph.GraphAPIError("Token expired", code=190, status=400))):
            with self.assertRaises(services.ReplyError):
                services.send_reply(comment.live_reply)
        self.page.connection.refresh_from_db()
        self.assertEqual(self.page.connection.status, FacebookConnection.Status.NEEDS_RECONNECT)

    def test_disconnected_page_cannot_send(self):
        comment = self.drafted()
        self.page.status = "AVAILABLE"
        self.page.save()
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            with self.assertRaises(services.ReplyError):
                services.send_reply(comment.live_reply)
        self.assertEqual(fake.calls, [])

    def test_person_edits_draft_and_sends_it_as_approved(self):
        comment = self.drafted()
        fake = FakeReplyGraph()
        with mock.patch("facebook.graph._open", fake):
            reply = services.reply_as_person(comment, message="Custom answer", user=self.owner)
        self.assertEqual(fake.posts()[0][3]["message"], "Custom answer")
        self.assertEqual((reply.source, reply.created_by), (Reply.Source.APPROVED, self.owner))
        self.assertEqual(Reply.objects.filter(comment=comment).count(), 1)

    def test_manual_reply_rules(self):
        comment = ingest(self.page, message="Nice")
        with self.assertRaises(services.ReplyError):
            services.reply_as_person(comment, message="   ", user=self.owner)
        with self.assertRaises(services.ReplyError):
            services.reply_as_person(comment, message="x" * 1001, user=self.owner)
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            services.reply_as_person(comment, message="Thanks!", user=self.owner)
            with self.assertRaises(services.ReplyError):
                services.reply_as_person(comment, message="Thanks again!", user=self.owner)

    def test_ignore_discards_draft_and_reopen_restores(self):
        comment = self.drafted()
        services.ignore_comment(comment, user=self.owner)
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.IGNORED)
        self.assertFalse(Reply.objects.filter(status="DRAFT").exists())
        services.reopen_comment(comment)
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.NEW)

    def test_stuck_sending_replies_are_recovered(self):
        comment = self.drafted()
        Reply.objects.filter(pk=comment.live_reply.pk).update(status="SENDING", sending_started_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual(services.recover_stuck_replies(), 1)
        self.assertEqual(Comment.objects.get(pk=comment.pk).status, Comment.Status.FAILED)

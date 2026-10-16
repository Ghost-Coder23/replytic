import io
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from automations.models import AutomationRule
from comments.models import Comment, Reply
from facebook.models import FacebookPage
from organizations.models import OrganizationMembership

from .helpers import add_member, make_org_with_owner
from .phase3 import FakeReplyGraph, comment_change, ingest, make_event, make_page, make_rule

Role = OrganizationMembership.Role


class Base(TestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Alpha Furniture", "owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Beta Phones", "owner-b@example.com")
        self.agent_a, _ = add_member(self.org_a, "agent-a@example.com", Role.AGENT)
        self.admin_a, _ = add_member(self.org_a, "admin-a@example.com", Role.ADMIN)
        for org in (self.org_a, self.org_b):
            org.whatsapp_number = "+263 77 000 0001"
            org.save()
        self.page_a = make_page(self.org_a, self.owner_a, "111", "Alpha Page")
        self.page_b = make_page(self.org_b, self.owner_b, "999", "Beta Page")
        self.rule_a = make_rule(self.org_a, name="Alpha price")
        self.rule_b = make_rule(self.org_b, name="Beta secret rule")
        self.client.force_login(self.owner_a)

    def comment(self, page, message="How much?", **kw):
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            return ingest(page, message=message, **kw)


class InboxTests(Base):
    def test_inbox_shows_only_own_comments(self):
        self.comment(self.page_a, "Alpha question", author_name="Alice A")
        self.comment(self.page_b, "Beta private question", author_name="Bob B")
        page = self.client.get(reverse("comments:index"))
        self.assertContains(page, "Alpha question")
        self.assertNotContains(page, "Beta private question")
        self.assertNotContains(page, "Bob B")

    def test_filters_and_counts(self):
        drafted = self.comment(self.page_a, "price?")
        replied = self.comment(self.page_a, "Nice photo")
        Comment.objects.filter(pk=replied.pk).update(status="REPLIED")
        ignored = self.comment(self.page_a, "spam")
        Comment.objects.filter(pk=ignored.pk).update(status="IGNORED")
        tabs = {t["key"]: t["count"] for t in self.client.get(reverse("comments:index")).context["tabs"]}
        self.assertEqual(tabs, {"attention": 1, "replied": 1, "ignored": 1, "all": 3})
        self.assertContains(self.client.get(reverse("comments:index"), {"filter": "replied"}), "Nice photo")
        self.assertNotContains(self.client.get(reverse("comments:index"), {"filter": "replied"}), "price?")
        self.assertEqual(self.client.get(reverse("comments:index"), {"filter": "bogus"}).context["active_filter"], "attention")

    def test_empty_states(self):
        self.page_a.delete()
        self.assertContains(self.client.get(reverse("comments:index")), "Connect Facebook")
        make_page(self.org_a, self.owner_a, "555", "New Page")
        self.assertContains(self.client.get(reverse("comments:index")), "watching your connected Page")

    def test_comment_text_is_escaped(self):
        self.comment(self.page_a, "<script>alert(1)</script> price")
        html = self.client.get(reverse("comments:index")).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_pagination(self):
        for i in range(25):
            self.comment(self.page_a, f"question {i}", comment_id=f"pg{i}")
        response = self.client.get(reverse("comments:index"), {"filter": "all"})
        self.assertEqual(len(response.context["comments"]), 20)
        self.assertEqual(len(self.client.get(reverse("comments:index"), {"filter": "all", "page": 2}).context["comments"]), 5)

    def test_anonymous_redirected_and_agent_can_view(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("comments:index")).status_code, 302)
        self.client.force_login(self.agent_a)
        self.assertEqual(self.client.get(reverse("comments:index")).status_code, 200)

    def test_check_button_processes_only_this_organizations_events(self):
        make_event(self.page_a, comment_change(comment_id="mine", message="hello"))
        make_event(self.page_b, comment_change(comment_id="theirs", message="hello"))
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            response = self.client.post(reverse("comments:check"), follow=True)
        self.assertContains(response, "1 update processed")
        self.assertTrue(Comment.objects.filter(facebook_comment_id="mine").exists())
        self.assertFalse(Comment.objects.filter(facebook_comment_id="theirs").exists())


class ReplyActionTests(Base):
    def test_approving_a_suggestion_posts_the_edited_text(self):
        comment = self.comment(self.page_a, "price?")
        fake = FakeReplyGraph()
        with mock.patch("facebook.graph._open", fake):
            response = self.client.post(reverse("comments:reply", args=[comment.pk]), {"message": "Edited answer", "next": "/comments/?filter=all"}, follow=True)
        self.assertContains(response, "Your reply was posted on Facebook.")
        self.assertEqual(fake.posts()[0][3]["message"], "Edited answer")
        self.assertEqual(response.redirect_chain[-1][0], "/comments/?filter=all")

    def test_agents_can_reply_by_hand(self):
        comment = self.comment(self.page_a, "Lovely")
        self.client.force_login(self.agent_a)
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            self.client.post(reverse("comments:reply", args=[comment.pk]), {"message": "Thank you!"})
        self.assertEqual(Reply.objects.get(comment=comment).created_by, self.agent_a)

    def test_empty_reply_shows_an_error(self):
        comment = self.comment(self.page_a, "Lovely")
        response = self.client.post(reverse("comments:reply", args=[comment.pk]), {"message": ""}, follow=True)
        self.assertContains(response, "Write a reply first.")

    def test_person_written_replies_do_not_count_as_automated(self):
        comment = self.comment(self.page_a, "price?")
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            self.client.post(reverse("comments:reply", args=[comment.pk]), {"message": "Sent by me"})
        metrics = {m["label"]: m["value"] for m in self.client.get(reverse("dashboard:index")).context["metrics"]}
        self.assertEqual(metrics["Automated replies"], 0)

    def test_ignore_reopen_discard(self):
        comment = self.comment(self.page_a, "price?")
        self.client.post(reverse("comments:discard", args=[comment.pk]))
        comment.refresh_from_db()
        self.assertEqual(comment.status, "NEW")
        self.client.post(reverse("comments:ignore", args=[comment.pk]))
        comment.refresh_from_db()
        self.assertEqual(comment.status, "IGNORED")
        self.client.post(reverse("comments:reopen", args=[comment.pk]))
        comment.refresh_from_db()
        self.assertEqual(comment.status, "NEW")

    def test_actions_are_post_only(self):
        comment = self.comment(self.page_a)
        for name in ("reply", "ignore", "reopen", "discard", "retry"):
            self.assertEqual(self.client.get(reverse(f"comments:{name}", args=[comment.pk])).status_code, 405)

    def test_next_url_cannot_redirect_off_site(self):
        comment = self.comment(self.page_a)
        for target in ("https://evil.example.com/comments/", "//evil.example.com", "/dashboard/"):
            response = self.client.post(reverse("comments:ignore", args=[comment.pk]), {"next": target})
            self.assertEqual(response["Location"], reverse("comments:index"))

    def test_other_organizations_comments_return_404_for_every_action(self):
        theirs = self.comment(self.page_b, "Beta private price")
        with mock.patch("facebook.graph._open", FakeReplyGraph()) as fake:
            for name in ("reply", "ignore", "reopen", "discard", "retry"):
                with self.subTest(action=name):
                    self.assertEqual(self.client.post(reverse(f"comments:{name}", args=[theirs.pk]), {"message": "x"}).status_code, 404)
        self.assertEqual(fake.calls, [])
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "DRAFTED")
        self.assertFalse(Reply.objects.filter(status="SENT").exists())

    def test_isolation_is_symmetric(self):
        mine = self.comment(self.page_a)
        self.client.force_login(self.owner_b)
        self.assertEqual(self.client.post(reverse("comments:ignore", args=[mine.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("comments:index"), {"filter": "all"}), "How much?")


class RuleManagementTests(Base):
    def valid(self, **extra):
        data = {"name": "Delivery", "page": "", "keywords": "deliver\nshipping\nDeliver", "match_mode": "ANY", "action": "REPLY",
                "exclude_keywords": "", "reply_template": "Yes {first_name}, we deliver.", "priority": 50, "is_active": "on"}
        data.update(extra)
        return data

    def test_create_rule_cleans_keywords(self):
        response = self.client.post(reverse("automations:create"), self.valid(), follow=True)
        self.assertContains(response, "created")
        rule = AutomationRule.objects.get(name="Delivery")
        self.assertEqual(rule.organization, self.org_a)
        self.assertEqual(rule.created_by, self.owner_a)
        self.assertEqual(rule.keyword_list, ["deliver", "shipping"])  # de-duplicated case-insensitively

    def test_validation(self):
        cases = {
            "keywords": ({"keywords": "  \n "}, "at least one keyword"),
            "unknown placeholder": ({"reply_template": "Hi {nickname}"}, "Unknown placeholder"),
            "missing reply": ({"reply_template": ""}, "Write the reply"),
            "too long": ({"reply_template": "x" * 1001}, "under 1000"),
        }
        for label, (data, expected) in cases.items():
            with self.subTest(case=label):
                self.assertContains(self.client.post(reverse("automations:create"), self.valid(**data)), expected)
        self.assertFalse(AutomationRule.objects.filter(name="Delivery").exists())

    def test_flag_rule_needs_no_reply_and_discards_template(self):
        self.client.post(reverse("automations:create"), self.valid(name="Complaints", action="FLAG", reply_template="ignored text"))
        self.assertEqual(AutomationRule.objects.get(name="Complaints").reply_template, "")

    def test_cannot_attach_a_rule_to_another_organizations_page(self):
        response = self.client.post(reverse("automations:create"), self.valid(page=str(self.page_b.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AutomationRule.objects.filter(name="Delivery").exists())

    def test_edit_toggle_delete(self):
        self.client.post(reverse("automations:edit", args=[self.rule_a.pk]), self.valid(name="Renamed"))
        self.rule_a.refresh_from_db()
        self.assertEqual(self.rule_a.name, "Renamed")
        self.client.post(reverse("automations:toggle", args=[self.rule_a.pk]))
        self.rule_a.refresh_from_db()
        self.assertFalse(self.rule_a.is_active)
        self.client.post(reverse("automations:delete", args=[self.rule_a.pk]))
        self.assertFalse(AutomationRule.objects.filter(pk=self.rule_a.pk).exists())

    def test_list_shows_only_own_rules(self):
        page = self.client.get(reverse("automations:index"))
        self.assertContains(page, "Alpha price")
        self.assertNotContains(page, "Beta secret rule")

    def test_other_organizations_rules_cannot_be_touched(self):
        for name, method in (("edit", "get"), ("edit", "post"), ("toggle", "post"), ("delete", "post")):
            with self.subTest(action=name, method=method):
                url = reverse(f"automations:{name}", args=[self.rule_b.pk])
                response = getattr(self.client, method)(url, self.valid(name="Hacked")) if method == "post" else self.client.get(url)
                self.assertEqual(response.status_code, 404)
        self.rule_b.refresh_from_db()
        self.assertEqual((self.rule_b.name, self.rule_b.is_active), ("Beta secret rule", True))

    def test_agents_cannot_manage_automations(self):
        self.client.force_login(self.agent_a)
        for name, args in (("index", []), ("create", []), ("edit", [self.rule_a.pk])):
            self.assertEqual(self.client.get(reverse(f"automations:{name}", args=args)).status_code, 403)
        self.assertEqual(self.client.post(reverse("automations:delete", args=[self.rule_a.pk])).status_code, 403)
        self.assertTrue(AutomationRule.objects.filter(pk=self.rule_a.pk).exists())

    def test_admins_can_manage_automations(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.post(reverse("automations:create"), self.valid()).status_code, 302)

    def test_test_box_shows_matching_rule_and_reply(self):
        response = self.client.post(reverse("automations:test"), {"message": "How much is the sofa?", "author_name": "Rudo Chari"})
        self.assertContains(response, "Alpha price")
        self.assertContains(response, "Hi Rudo, thanks for asking!")
        self.assertEqual(Comment.objects.count(), 0)  # a dry run stores nothing

    def test_test_box_when_nothing_matches(self):
        response = self.client.post(reverse("automations:test"), {"message": "Lovely photo", "author_name": "X"})
        self.assertContains(response, "No rule matches")


class PageModeTests(Base):
    def post(self, page, **data):
        return self.client.post(reverse("automations:page_mode", args=[page.pk]), data, follow=True)

    def test_auto_requires_explicit_confirmation(self):
        response = self.post(self.page_a, mode="AUTO")
        self.assertContains(response, "Confirm that you want Replytic to reply publicly")
        self.page_a.refresh_from_db()
        self.assertEqual(self.page_a.automation_mode, "DRAFT")

    def test_owner_can_enable_auto_with_confirmation_and_limit(self):
        self.post(self.page_a, mode="AUTO", confirm_auto="on", max_auto_replies_per_hour="5")
        self.page_a.refresh_from_db()
        self.assertEqual((self.page_a.automation_mode, self.page_a.max_auto_replies_per_hour), ("AUTO", 5))

    def test_can_go_back_to_draft_or_off_without_confirmation(self):
        self.post(self.page_a, mode="OFF")
        self.page_a.refresh_from_db()
        self.assertEqual(self.page_a.automation_mode, "OFF")

    def test_invalid_mode_rejected(self):
        self.post(self.page_a, mode="YOLO")
        self.page_a.refresh_from_db()
        self.assertEqual(self.page_a.automation_mode, "DRAFT")

    def test_other_organizations_pages_return_404(self):
        response = self.client.post(reverse("automations:page_mode", args=[self.page_b.pk]), {"mode": "OFF"})
        self.assertEqual(response.status_code, 404)
        self.page_b.refresh_from_db()
        self.assertEqual(self.page_b.automation_mode, "DRAFT")

    def test_agents_cannot_change_modes(self):
        self.client.force_login(self.agent_a)
        self.assertEqual(self.client.post(reverse("automations:page_mode", args=[self.page_a.pk]), {"mode": "OFF"}).status_code, 403)


class DashboardIntegrationTests(Base):
    def test_metrics_reflect_real_activity_and_stay_per_tenant(self):
        self.page_a.automation_mode = "AUTO"
        self.page_a.save()
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            ingest(self.page_a, message="price?")              # auto replied
            ingest(self.page_a, message="Lovely photo")        # needs attention
            ingest(self.page_b, message="Beta only")           # other tenant
        metrics = {m["label"]: m["value"] for m in self.client.get(reverse("dashboard:index")).context["metrics"]}
        self.assertEqual(metrics, {"Comments today": 2, "Automated replies": 1, "Potential leads": 0, "Needs attention": 1})

    def test_checklist_reflects_rules_and_pages(self):
        items = {i["label"]: i["state"] for i in self.client.get(reverse("dashboard:index")).context["checklist"]["items"]}
        self.assertEqual(items["Connect Facebook Page"], "done")
        self.assertEqual(items["Create first automation"], "done")
        self.assertEqual(items["Enable automation"], "done")

    def test_recent_activity_includes_comments(self):
        self.comment(self.page_a, "hello", author_name="Alice A")
        self.assertContains(self.client.get(reverse("dashboard:index")), "Alice A commented on Alpha Page")


class CommandTests(Base):
    def test_process_comments_command_runs_the_pipeline(self):
        make_event(self.page_a, comment_change(comment_id="cmd-1", message="price?"))
        out = io.StringIO()
        with mock.patch("facebook.graph._open", FakeReplyGraph()):
            call_command("process_comments", stdout=out)
        self.assertIn("Processed 1", out.getvalue())
        self.assertTrue(Comment.objects.filter(facebook_comment_id="cmd-1").exists())
        out = io.StringIO()
        call_command("process_comments", stdout=out)
        self.assertIn("Nothing to process", out.getvalue())

    def test_webhook_to_comment_end_to_end(self):
        import hashlib, hmac, json
        from django.test import override_settings
        body = json.dumps({"object": "page", "entry": [{"id": "111", "time": 1, "changes": [comment_change(comment_id="e2e", message="How much?")]}]}).encode()
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        with override_settings(META_APP_SECRET="secret"):
            response = self.client.post(reverse("facebook:webhook"), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
        self.assertEqual(response.status_code, 200)
        call_command("process_comments", stdout=io.StringIO())
        comment = Comment.objects.get(facebook_comment_id="e2e")
        self.assertEqual((comment.organization, comment.status), (self.org_a, "DRAFTED"))

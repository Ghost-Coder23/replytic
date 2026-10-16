from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from automations import engine
from automations.models import AutomationRule

from .helpers import make_org_with_owner
from .phase3 import make_page, make_rule


class KeywordMatchingTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")

    def rule(self, **kw):
        return make_rule(self.org, **kw)

    def test_case_insensitive_whole_word_matching(self):
        rule = self.rule(keywords="price")
        self.assertTrue(engine.rule_matches(rule, "What is the PRICE?"))
        self.assertFalse(engine.rule_matches(rule, "It is priceless"))
        self.assertFalse(engine.rule_matches(rule, "unpriced"))

    def test_phrases_and_curly_quotes(self):
        rule = self.rule(keywords="how much\nwhat\u2019s the cost")
        self.assertTrue(engine.rule_matches(rule, "Hi, HOW   MUCH for this?"))
        self.assertTrue(engine.rule_matches(rule, "what's the cost"))

    def test_non_english_keywords_work(self):
        rule = self.rule(keywords="imarii")
        self.assertTrue(engine.rule_matches(rule, "Imarii sofa iyi?"))

    def test_all_mode_needs_every_keyword(self):
        rule = self.rule(keywords="price\ndelivery", match_mode="ALL")
        self.assertFalse(engine.rule_matches(rule, "price?"))
        self.assertTrue(engine.rule_matches(rule, "price and delivery?"))

    def test_exclude_keywords_skip_the_rule(self):
        rule = self.rule(keywords="price", exclude_keywords="not interested")
        self.assertFalse(engine.rule_matches(rule, "price? not interested"))

    def test_regex_characters_in_keywords_are_literal(self):
        rule = self.rule(keywords="$5.00 (sale)")
        self.assertTrue(engine.rule_matches(rule, "is the $5.00 (sale) still on"))
        self.assertFalse(engine.rule_matches(self.rule(keywords=".*"), "anything"))

    def test_empty_keywords_never_match(self):
        self.assertFalse(engine.rule_matches(self.rule(keywords="  \n "), "anything"))


class DecisionTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Alpha Furniture", "o@example.com")
        self.org.phone = "+263 77 000 0000"
        self.org.save()
        self.page = make_page(self.org, self.owner)

    def decide(self, message, rules, page=None):
        return engine.decide(message=message, author_name="Tendai Moyo", page=page or self.page, organization=self.org, rules=rules)

    def test_first_rule_by_priority_wins(self):
        late = make_rule(self.org, name="Late", priority=200, template="late")
        early = make_rule(self.org, name="Early", priority=10, template="early")
        self.assertEqual(self.decide("price?", [late, early]).rule, early)

    def test_inactive_and_other_page_rules_are_skipped(self):
        other_page = make_page(self.org, self.owner, page_id="222", name="Other")
        inactive = make_rule(self.org, name="Off", is_active=False)
        elsewhere = make_rule(self.org, name="Elsewhere", page=other_page)
        self.assertIsNone(self.decide("price?", [inactive, elsewhere]))
        everywhere = make_rule(self.org, name="Everywhere")
        self.assertEqual(self.decide("price?", [inactive, elsewhere, everywhere]).rule, everywhere)

    def test_other_organizations_rules_are_never_used(self):
        other_org, other_owner, _ = make_org_with_owner("Beta", "b@example.com")
        foreign = make_rule(other_org, name="Foreign")
        self.assertIsNone(self.decide("price?", [foreign]))

    def test_placeholders_are_filled(self):
        rule = make_rule(self.org, template="Hi {first_name}! {business_name} on {page_name}: call {phone}")
        decision = self.decide("price?", [rule])
        self.assertEqual(decision.text, "Hi Tendai! Alpha Furniture on Alpha Page: call +263 77 000 0000")
        self.assertEqual(decision.missing, ())

    def test_missing_placeholder_is_reported_and_left_visible(self):
        rule = make_rule(self.org, template="WhatsApp us on {whatsapp}")
        decision = self.decide("price?", [rule])
        self.assertEqual(decision.missing, ("whatsapp",))
        self.assertIn("{whatsapp}", decision.text)

    def test_unknown_placeholder_counts_as_missing(self):
        decision = self.decide("price?", [make_rule(self.org, template="Hi {nickname}")])
        self.assertEqual(decision.missing, ("nickname",))

    def test_flag_and_ignore_rules_have_no_text(self):
        rule = make_rule(self.org, keywords="refund", action="FLAG", template="")
        decision = self.decide("I want a refund", [rule])
        self.assertEqual((decision.action, decision.text), ("FLAG", ""))

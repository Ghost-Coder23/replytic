from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, OrganizationMembership
from organizations.services import SESSION_KEY, create_organization

from .helpers import add_member, make_org_with_owner, make_user

Role = OrganizationMembership.Role


class ModelTests(TestCase):
    def test_organization_creation_generates_unique_slugs(self):
        a = Organization.objects.create(name="Harare Phones")
        b = Organization.objects.create(name="Harare Phones")
        self.assertEqual(a.slug, "harare-phones")
        self.assertEqual(b.slug, "harare-phones-2")
        self.assertEqual(str(a), "Harare Phones")

    def test_membership_is_unique_per_user_and_organization(self):
        org, owner, _ = make_org_with_owner("Brandon Furniture", "b@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.create(user=owner, organization=org, role=Role.AGENT)

    def test_create_organization_makes_owner_membership(self):
        user = make_user("owner@example.com")
        org, membership = create_organization(owner=user, name="Sunrise Hardware")
        self.assertEqual(membership.role, Role.OWNER)
        self.assertEqual(membership.organization, org)

    def test_user_can_belong_to_many_organizations(self):
        user = make_user("multi@example.com")
        create_organization(owner=user, name="One")
        create_organization(owner=user, name="Two")
        self.assertEqual(user.memberships.count(), 2)


class CurrentOrganizationTests(TestCase):
    def setUp(self):
        self.user = make_user("multi@example.com", first_name="Multi")
        self.org_one, _ = create_organization(owner=self.user, name="Brandon Furniture")
        self.org_two, _ = create_organization(owner=self.user, name="Harare Phones")
        self.other_org, self.other_owner, _ = make_org_with_owner("Prime Cars", "cars@example.com")
        self.client.force_login(self.user)

    def test_single_membership_is_selected_automatically(self):
        client = self.client_class()
        loner = make_user("loner@example.com")
        org, _ = create_organization(owner=loner, name="Solo Shop")
        client.force_login(loner)
        response = client.get(reverse("dashboard:index"))
        self.assertEqual(response.wsgi_request.organization, org)

    def test_defaults_to_first_membership_for_multi_org_user(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.wsgi_request.organization, self.org_one)

    def test_switcher_lists_only_own_organizations(self):
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Harare Phones")
        self.assertNotContains(response, "Prime Cars")

    def test_switching_updates_session_and_current_organization(self):
        response = self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_two.pk)})
        self.assertRedirects(response, reverse("dashboard:index"))
        self.assertEqual(self.client.session[SESSION_KEY], str(self.org_two.pk))
        page = self.client.get(reverse("dashboard:index"))
        self.assertEqual(page.wsgi_request.organization, self.org_two)

    def test_cannot_switch_to_organization_user_does_not_belong_to(self):
        self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_two.pk)})
        response = self.client.post(reverse("organizations:switch"), {"organization_id": str(self.other_org.pk)}, follow=True)
        self.assertContains(response, "don&#x27;t have access")
        self.assertEqual(self.client.session[SESSION_KEY], str(self.org_two.pk))

    def test_switch_with_garbage_id_is_rejected_safely(self):
        response = self.client.post(reverse("organizations:switch"), {"organization_id": "not-a-uuid"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "don&#x27;t have access")

    def test_tampered_session_falls_back_to_a_real_membership(self):
        session = self.client.session
        session[SESSION_KEY] = str(self.other_org.pk)
        session.save()
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.wsgi_request.organization, self.org_one)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.org_one.pk))

    def test_switch_only_allows_post(self):
        self.assertEqual(self.client.get(reverse("organizations:switch")).status_code, 405)

    def test_switch_ignores_external_next_url(self):
        response = self.client.post(reverse("organizations:switch"), {
            "organization_id": str(self.org_two.pk), "next": "https://evil.example.com/",
        })
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_inactive_organization_is_not_selectable(self):
        self.org_two.is_active = False
        self.org_two.save()
        self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_two.pk)})
        self.assertNotEqual(self.client.session[SESSION_KEY], str(self.org_two.pk))

    def test_create_additional_organization_switches_to_it(self):
        response = self.client.post(reverse("organizations:create"), {"name": "Sunrise Hardware"})
        self.assertRedirects(response, reverse("dashboard:index"))
        new_org = Organization.objects.get(name="Sunrise Hardware")
        self.assertEqual(new_org.memberships.get(user=self.user).role, Role.OWNER)
        self.assertEqual(self.client.session[SESSION_KEY], str(new_org.pk))

    def test_user_without_organization_is_sent_to_create_one(self):
        client = self.client_class()
        client.force_login(make_user("nobody@example.com"))
        self.assertRedirects(client.get(reverse("dashboard:index")), reverse("organizations:create"))

"""
Tenant isolation: the most important guarantee in Replytic.

Two businesses, each with an owner, an admin and an agent. Nothing from one
may be visible or changeable from the other, whatever IDs are submitted.
"""
from django.test import TestCase
from django.urls import reverse

from organizations.models import Organization, OrganizationMembership

from .helpers import add_member, make_org_with_owner

Role = OrganizationMembership.Role


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.org_a, self.owner_a, self.m_owner_a = make_org_with_owner("Alpha Furniture", "owner-a@example.com")
        self.org_b, self.owner_b, self.m_owner_b = make_org_with_owner("Beta Phones", "owner-b@example.com")
        self.agent_a, self.m_agent_a = add_member(self.org_a, "agent-a@example.com", Role.AGENT, "AgentA")
        self.agent_b, self.m_agent_b = add_member(self.org_b, "agent-b@example.com", Role.AGENT, "AgentB")

    def test_team_page_only_lists_own_organization_members(self):
        self.client.force_login(self.owner_a)
        response = self.client.get(reverse("organizations:team"))
        self.assertContains(response, "owner-a@example.com")
        self.assertContains(response, "agent-a@example.com")
        self.assertNotContains(response, "owner-b@example.com")
        self.assertNotContains(response, "agent-b@example.com")

        self.client.force_login(self.owner_b)
        response = self.client.get(reverse("organizations:team"))
        self.assertContains(response, "owner-b@example.com")
        self.assertNotContains(response, "agent-a@example.com")

    def test_dashboard_shows_only_own_business(self):
        self.client.force_login(self.owner_a)
        response = self.client.get(reverse("dashboard:index"))
        self.assertContains(response, "Alpha Furniture")
        self.assertNotContains(response, "Beta Phones")

    def test_changing_a_membership_id_in_the_url_cannot_touch_another_organization(self):
        self.client.force_login(self.owner_a)
        url = reverse("organizations:change_role", args=[self.m_agent_b.pk])
        response = self.client.post(url, {"role": Role.ADMIN})
        self.assertEqual(response.status_code, 404)
        self.m_agent_b.refresh_from_db()
        self.assertEqual(self.m_agent_b.role, Role.AGENT)

    def test_cannot_remove_a_member_of_another_organization_by_id(self):
        self.client.force_login(self.owner_a)
        response = self.client.post(reverse("organizations:remove_member", args=[self.m_agent_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(OrganizationMembership.objects.filter(pk=self.m_agent_b.pk).exists())

    def test_cross_tenant_isolation_is_symmetric(self):
        self.client.force_login(self.owner_b)
        for membership in (self.m_agent_a, self.m_owner_a):
            with self.subTest(target=membership.user.email):
                self.assertEqual(
                    self.client.post(reverse("organizations:change_role", args=[membership.pk]), {"role": Role.AGENT}).status_code, 404
                )
                self.assertEqual(
                    self.client.post(reverse("organizations:remove_member", args=[membership.pk])).status_code, 404
                )
        self.assertEqual(OrganizationMembership.objects.filter(organization=self.org_a).count(), 2)

    def test_business_profile_edit_only_changes_current_organization(self):
        self.client.force_login(self.owner_a)
        # Attacker-style extra fields must be ignored; the instance comes from the verified session.
        response = self.client.post(reverse("organizations:settings"), {
            "form_name": "business", "name": "Alpha Renamed", "timezone": "Africa/Harare",
            "id": str(self.org_b.pk), "organization_id": str(self.org_b.pk),
        })
        self.assertEqual(response.status_code, 302)
        self.org_a.refresh_from_db()
        self.org_b.refresh_from_db()
        self.assertEqual(self.org_a.name, "Alpha Renamed")
        self.assertEqual(self.org_b.name, "Beta Phones")

    def test_user_cannot_switch_into_another_tenant(self):
        self.client.force_login(self.owner_a)
        self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_b.pk)})
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.wsgi_request.organization, self.org_a)
        self.assertNotContains(response, "Beta Phones")

    def test_forged_session_cannot_expose_another_tenant(self):
        self.client.force_login(self.owner_a)
        session = self.client.session
        session["current_organization_id"] = str(self.org_b.pk)
        session.save()
        response = self.client.get(reverse("organizations:team"))
        self.assertContains(response, "owner-a@example.com")
        self.assertNotContains(response, "owner-b@example.com")

    def test_user_in_both_organizations_sees_data_for_selected_one_only(self):
        both, _ = add_member(self.org_a, "both@example.com", Role.ADMIN, "Both")
        OrganizationMembership.objects.create(user=both, organization=self.org_b, role=Role.ADMIN)
        self.client.force_login(both)
        self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_a.pk)})
        page = self.client.get(reverse("organizations:team"))
        self.assertContains(page, "owner-a@example.com")
        self.assertNotContains(page, "owner-b@example.com")
        self.client.post(reverse("organizations:switch"), {"organization_id": str(self.org_b.pk)})
        page = self.client.get(reverse("organizations:team"))
        self.assertContains(page, "owner-b@example.com")
        self.assertNotContains(page, "owner-a@example.com")

    def test_removed_member_loses_access_immediately(self):
        self.client.force_login(self.owner_a)
        self.client.post(reverse("organizations:remove_member", args=[self.m_agent_a.pk]))
        self.client.force_login(self.agent_a)
        # Agent no longer belongs to any business.
        self.assertRedirects(self.client.get(reverse("dashboard:index")), reverse("organizations:create"))

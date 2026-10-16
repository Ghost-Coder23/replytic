from django.test import TestCase
from django.urls import reverse

from organizations.models import OrganizationMembership
from organizations.permissions import has_permission

from .helpers import add_member, make_org_with_owner

Role = OrganizationMembership.Role

ADMIN_ONLY_PAGES = ["automations:index", "dashboard:knowledge", "facebook:index", "dashboard:analytics", "dashboard:billing"]
OPEN_PAGES = ["dashboard:index", "comments:index", "dashboard:leads", "organizations:team", "organizations:settings"]


class RolePermissionTests(TestCase):
    def setUp(self):
        self.org, self.owner, self.m_owner = make_org_with_owner("Alpha Furniture", "owner@example.com")
        self.admin, self.m_admin = add_member(self.org, "admin@example.com", Role.ADMIN, "Admin")
        self.agent, self.m_agent = add_member(self.org, "agent@example.com", Role.AGENT, "Agent")

    def test_owner_and_admin_reach_every_page(self):
        for user in (self.owner, self.admin):
            self.client.force_login(user)
            for name in ADMIN_ONLY_PAGES + OPEN_PAGES:
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_agent_reaches_operational_pages_only(self):
        self.client.force_login(self.agent)
        for name in OPEN_PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ADMIN_ONLY_PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_agent_sidebar_hides_admin_sections(self):
        self.client.force_login(self.agent)
        html = self.client.get(reverse("dashboard:index")).content.decode()
        self.assertIn(reverse("comments:index"), html)
        self.assertNotIn(f'href="{reverse("dashboard:billing")}"', html)
        self.assertNotIn(f'href="{reverse("dashboard:analytics")}"', html)

    def test_sidebar_marks_active_page_from_url_name(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("organizations:team")).content.decode()
        self.assertRegex(html, r'href="/team/" class="nav-link-rp active"')
        self.assertNotRegex(html, r'href="/leads/" class="nav-link-rp active"')

    def test_agent_cannot_edit_business_profile(self):
        self.client.force_login(self.agent)
        response = self.client.post(reverse("organizations:settings"), {"form_name": "business", "name": "Hacked", "timezone": "Africa/Harare"})
        self.assertEqual(response.status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Alpha Furniture")

    def test_agent_settings_only_shows_personal_tabs(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("organizations:settings") + "?tab=business")
        self.assertEqual(response.context["active_tab"], "account")
        self.assertNotContains(response, "Business profile")

    def test_agent_cannot_manage_team(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.post(reverse("organizations:remove_member", args=[self.m_admin.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("organizations:change_role", args=[self.m_admin.pk]), {"role": Role.AGENT}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("organizations:team")), "Change Role")

    def test_admin_can_remove_agent_but_not_owner_or_change_roles(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("organizations:change_role", args=[self.m_agent.pk]), {"role": Role.ADMIN}).status_code, 403)
        self.assertEqual(self.client.post(reverse("organizations:remove_member", args=[self.m_owner.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("organizations:remove_member", args=[self.m_agent.pk])).status_code, 302)
        self.assertFalse(OrganizationMembership.objects.filter(pk=self.m_agent.pk).exists())

    def test_owner_can_change_role(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("organizations:change_role", args=[self.m_agent.pk]), {"role": Role.ADMIN})
        self.m_agent.refresh_from_db()
        self.assertEqual(self.m_agent.role, Role.ADMIN)

    def test_invalid_role_is_rejected(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("organizations:change_role", args=[self.m_agent.pk]), {"role": "SUPERUSER"})
        self.m_agent.refresh_from_db()
        self.assertEqual(self.m_agent.role, Role.AGENT)

    def test_owner_cannot_change_own_role_or_remove_self(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("organizations:change_role", args=[self.m_owner.pk]), {"role": Role.AGENT}).status_code, 403)
        self.assertEqual(self.client.post(reverse("organizations:remove_member", args=[self.m_owner.pk])).status_code, 403)

    def test_last_owner_is_protected(self):
        second, m_second = add_member(self.org, "owner2@example.com", Role.OWNER, "Second")
        self.client.force_login(second)
        # Demote the original owner (allowed: another owner remains) ...
        self.client.post(reverse("organizations:change_role", args=[self.m_owner.pk]), {"role": Role.ADMIN})
        self.m_owner.refresh_from_db()
        self.assertEqual(self.m_owner.role, Role.ADMIN)
        # ... but the sole remaining owner cannot be removed or demoted by anyone.
        from organizations.services import MembershipError, change_member_role, remove_member
        with self.assertRaises(MembershipError):
            remove_member(membership=m_second)
        with self.assertRaises(MembershipError):
            change_member_role(membership=m_second, new_role=Role.AGENT)

    def test_permission_helper(self):
        self.assertTrue(has_permission(self.m_owner, "manage_billing"))
        self.assertFalse(has_permission(self.m_admin, "manage_billing"))
        self.assertFalse(has_permission(self.m_agent, "manage_team"))
        self.assertFalse(has_permission(None, "view_dashboard"))

    def test_403_page_uses_replytic_design(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:billing"))
        self.assertContains(response, "have access to this page", status_code=403)

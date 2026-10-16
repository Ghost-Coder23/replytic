from django.views.generic import TemplateView

from core.mixins import OrganizationRequiredMixin

from . import services


class DashboardView(OrganizationRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"
    required_permission = "view_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(services.build_dashboard_context(
            user=self.request.user, organization=self.organization, membership=self.membership,
        ))
        return context


class ModulePageView(OrganizationRequiredMixin, TemplateView):
    """A future module's placeholder page. Real logic will live in its own app."""


class LeadsView(ModulePageView):
    template_name = "dashboard/leads.html"
    required_permission = "manage_leads"


class KnowledgeView(ModulePageView):
    template_name = "dashboard/knowledge.html"
    required_permission = "manage_knowledge"

    TOPICS = [
        ("bi-building", "Business information"),
        ("bi-question-circle", "FAQs"),
        ("bi-box-seam", "Products"),
        ("bi-tag", "Prices"),
        ("bi-truck", "Delivery information"),
        ("bi-clock", "Opening hours"),
        ("bi-telephone", "Contact details"),
        ("bi-file-earmark-text", "Policies"),
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["topics"] = self.TOPICS
        return context


class AnalyticsView(ModulePageView):
    template_name = "dashboard/analytics.html"
    required_permission = "view_analytics"


class BillingView(ModulePageView):
    template_name = "dashboard/billing.html"
    required_permission = "view_billing"

    PLANS = [
        ("Starter", "For a single Page getting started."),
        ("Business", "For growing teams with more conversations."),
        ("Pro", "For businesses running several Pages."),
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["plans"] = self.PLANS
        return context

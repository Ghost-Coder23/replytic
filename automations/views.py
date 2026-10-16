from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import FormView, TemplateView

from core.mixins import OrganizationRequiredMixin
from facebook.models import FacebookPage

from . import engine
from .forms import AutomationRuleForm, PageModeForm, TestCommentForm
from .models import AutomationRule


class AutomationMixin(OrganizationRequiredMixin):
    required_permission = "manage_automations"

    def rules(self):
        # Tenant isolation: every rule query starts from the current organization.
        return AutomationRule.objects.filter(organization=self.organization).select_related("page")


class IndexView(AutomationMixin, TemplateView):
    template_name = "automations/index.html"
    test_result = None
    test_form = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pages = list(FacebookPage.objects.filter(organization=self.organization, status=FacebookPage.Status.CONNECTED))
        context.update({
            "rules": list(self.rules()),
            "pages": pages,
            "mode_choices": FacebookPage.AutomationMode.choices,
            "test_form": self.test_form or TestCommentForm(),
            "test_result": self.test_result,
            "placeholders": engine.PLACEHOLDERS,
        })
        return context


class TestRulesView(IndexView):
    """Dry run: shows which rule would answer a sample comment. Nothing is saved or sent."""

    def post(self, request):
        form = TestCommentForm(request.POST)
        if form.is_valid():
            rules = list(self.rules().filter(is_active=True))
            page = FacebookPage.objects.filter(organization=self.organization, status=FacebookPage.Status.CONNECTED).first()
            decision = engine.decide(
                message=form.cleaned_data["message"], author_name=form.cleaned_data["author_name"],
                page=page, organization=self.organization, rules=rules,
            )
            self.test_result = {"decision": decision, "message": form.cleaned_data["message"]}
        self.test_form = form
        return render(request, self.template_name, self.get_context_data())

    http_method_names = ["post"]


class RuleCreateView(AutomationMixin, FormView):
    template_name = "automations/rule_form.html"
    form_class = AutomationRuleForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs

    def form_valid(self, form):
        rule = form.save(commit=False)
        rule.organization = self.organization
        rule.created_by = self.request.user
        rule.save()
        messages.success(self.request, f"Rule “{rule.name}” created.")
        return redirect("automations:index")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"placeholders": engine.PLACEHOLDERS, "editing": False})
        return context


class RuleUpdateView(RuleCreateView):
    def dispatch(self, request, *args, **kwargs):
        self.rule = None
        return super().dispatch(request, *args, **kwargs)

    def get_rule(self):
        if self.rule is None:
            self.rule = get_object_or_404(self.rules(), pk=self.kwargs["rule_id"])
        return self.rule

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_rule()
        return kwargs

    def form_valid(self, form):
        rule = form.save()
        messages.success(self.request, f"Rule “{rule.name}” saved.")
        return redirect("automations:index")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"editing": True, "rule": self.get_rule()})
        return context


class _RuleAction(AutomationMixin, View):
    http_method_names = ["post"]

    def get_rule(self, rule_id):
        return get_object_or_404(self.rules(), pk=rule_id)


class RuleDeleteView(_RuleAction):
    def post(self, request, rule_id):
        rule = self.get_rule(rule_id)
        name = rule.name
        rule.delete()
        messages.success(request, f"Rule “{name}” deleted.")
        return redirect("automations:index")


class RuleToggleView(_RuleAction):
    def post(self, request, rule_id):
        rule = self.get_rule(rule_id)
        rule.is_active = not rule.is_active
        rule.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"Rule “{rule.name}” is now {'active' if rule.is_active else 'paused'}.")
        return redirect("automations:index")


class PageModeView(AutomationMixin, View):
    """Choose how Replytic behaves on a Page: off, suggest, or reply automatically."""

    http_method_names = ["post"]

    def post(self, request, page_id):
        page = get_object_or_404(FacebookPage, pk=page_id, organization=self.organization)
        form = PageModeForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("automations:index")
        page.automation_mode = form.cleaned_data["mode"]
        limit = form.cleaned_data.get("max_auto_replies_per_hour")
        if limit:
            page.max_auto_replies_per_hour = limit
        page.save(update_fields=["automation_mode", "max_auto_replies_per_hour", "updated_at"])
        messages.success(request, f"{page.name}: {page.get_automation_mode_display().lower()}.")
        return redirect("automations:index")

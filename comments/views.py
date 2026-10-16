from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from core.mixins import OrganizationRequiredMixin
from facebook.models import FacebookPage

from . import services
from .models import Comment

FILTERS = {
    "attention": ("Needs attention", Q(status__in=Comment.NEEDS_ATTENTION)),
    "replied": ("Replied", Q(status=Comment.Status.REPLIED)),
    "ignored": ("Ignored", Q(status=Comment.Status.IGNORED)),
    "all": ("All", Q()),
}
PAGE_SIZE = 20


class CommentsIndexView(OrganizationRequiredMixin, TemplateView):
    template_name = "comments/index.html"
    required_permission = "view_comments"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Tenant isolation: everything starts from the current organization.
        base = Comment.objects.filter(organization=self.organization, is_deleted=False)
        active = self.request.GET.get("filter", "attention")
        if active not in FILTERS:
            active = "attention"
        counts = base.aggregate(**{key: Count("pk", filter=condition) for key, (_, condition) in FILTERS.items()})
        queryset = base.filter(FILTERS[active][1]).select_related("page", "matched_rule").prefetch_related("replies")
        page = Paginator(queryset, PAGE_SIZE).get_page(self.request.GET.get("page"))
        connected = FacebookPage.objects.filter(organization=self.organization, status=FacebookPage.Status.CONNECTED).exists()
        context.update({
            "tabs": [{"key": key, "label": label, "count": counts[key], "active": key == active} for key, (label, _) in FILTERS.items()],
            "active_filter": active,
            "page_obj": page,
            "comments": page.object_list,
            "has_connected_page": connected,
            "total_comments": counts["all"],
            "max_reply_length": 1000,
        })
        return context


class _CommentAction(OrganizationRequiredMixin, View):
    http_method_names = ["post"]
    required_permission = "reply_to_comments"

    def get_comment(self, comment_id):
        # A comment id from another organization simply doesn't exist here (404).
        return get_object_or_404(
            Comment.objects.select_related("page__connection", "organization"),
            pk=comment_id, organization=self.organization,
        )

    def back(self):
        target = self.request.POST.get("next", "")
        if target and url_has_allowed_host_and_scheme(target, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()) and target.startswith("/comments/"):
            return redirect(target)
        return redirect("comments:index")


class ReplyView(_CommentAction):
    def post(self, request, comment_id):
        comment = self.get_comment(comment_id)
        try:
            services.reply_as_person(comment, message=request.POST.get("message", ""), user=request.user)
        except services.ReplyError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Your reply was posted on Facebook.")
        return self.back()


class RetryView(_CommentAction):
    def post(self, request, comment_id):
        comment = self.get_comment(comment_id)
        try:
            services.retry_failed_reply(comment, user=request.user)
        except services.ReplyError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "The reply was posted on Facebook.")
        return self.back()


class IgnoreView(_CommentAction):
    def post(self, request, comment_id):
        services.ignore_comment(self.get_comment(comment_id), user=request.user)
        messages.success(request, "Comment ignored.")
        return self.back()


class ReopenView(_CommentAction):
    def post(self, request, comment_id):
        services.reopen_comment(self.get_comment(comment_id))
        messages.success(request, "Comment moved back to Needs attention.")
        return self.back()


class DiscardDraftView(_CommentAction):
    def post(self, request, comment_id):
        services.discard_draft(self.get_comment(comment_id), user=request.user)
        messages.success(request, "Suggested reply discarded.")
        return self.back()


class CheckNewView(OrganizationRequiredMixin, View):
    """Process this business's waiting Facebook updates now (no background worker needed)."""

    http_method_names = ["post"]
    required_permission = "view_comments"

    def post(self, request):
        handled = services.process_pending_events(organization=self.organization)
        messages.success(request, f"Checked for new comments. {handled} update{'s' if handled != 1 else ''} processed." if handled else "You're up to date. No new comments were waiting.")
        return redirect("comments:index")

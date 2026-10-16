import json
import logging

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from core.mixins import OrganizationRequiredMixin

from . import services
from .models import FacebookConnection, FacebookDeletionRequest, FacebookPage

logger = logging.getLogger(__name__)


class FacebookMixin(OrganizationRequiredMixin):
    required_permission = "manage_integrations"


class FacebookIndexView(FacebookMixin, TemplateView):
    template_name = "facebook/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(services.organization_summary(self.organization))
        context.update({
            "configured": services.is_configured(),
            # Only Replytic's own staff can change server settings; customers must never be told to edit .env.
            "is_operator": self.request.user.is_staff,
            "webhook_ready": services.webhook_is_configured(),
            "setup_checklist": services.setup_checklist(),
            "callback_url": services.callback_url(),
            "webhook_url": services.webhook_url(),
            "deauthorize_url": services.deauthorize_url(),
            "data_deletion_url": services.data_deletion_url(),
        })
        return context


class ConnectView(FacebookMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            url = services.start_oauth(session=request.session, user=request.user, organization=self.organization)
        except services.FacebookIntegrationError as error:
            messages.error(request, str(error))
            return redirect("facebook:index")
        return redirect(url)


class CallbackView(FacebookMixin, View):
    """Meta redirects the browser here. The user must still be signed in as the same member."""

    http_method_names = ["get"]

    def get(self, request):
        if request.GET.get("error"):
            request.session.pop(services.OAUTH_SESSION_KEY, None)
            messages.warning(request, "Facebook sign-in was cancelled. Nothing was connected.")
            return redirect("facebook:index")
        try:
            connection = services.complete_oauth(
                session=request.session, user=request.user, organization=self.organization,
                code=request.GET.get("code", ""), state=request.GET.get("state", ""),
            )
        except services.FacebookIntegrationError as error:
            messages.error(request, str(error))
            return redirect("facebook:index")
        messages.success(request, f"Connected as {connection.facebook_user_name}. Choose which Pages Replytic should watch.")
        return redirect("facebook:index")


class RefreshPagesView(FacebookMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        # Only this organization's connections are ever touched.
        for connection in FacebookConnection.objects.filter(organization=self.organization):
            try:
                services.sync_pages(connection)
            except services.FacebookIntegrationError as error:
                messages.error(request, str(error))
                break
        else:
            messages.success(request, "Your Pages are up to date.")
        return redirect("facebook:index")


class _PageActionView(FacebookMixin, View):
    http_method_names = ["post"]

    def get_page(self, page_id):
        # An id from another organization does not exist here (404).
        return get_object_or_404(FacebookPage, pk=page_id, organization=self.organization)


class ConnectPageView(_PageActionView):
    def post(self, request, page_id):
        page = self.get_page(page_id)
        try:
            services.connect_page(page)
        except services.FacebookIntegrationError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Replytic is now watching {page.name}.")
        return redirect("facebook:index")


class DisconnectPageView(_PageActionView):
    def post(self, request, page_id):
        page = self.get_page(page_id)
        services.disconnect_page(page)
        messages.success(request, f"Replytic stopped watching {page.name}.")
        return redirect("facebook:index")


class DisconnectAccountView(FacebookMixin, View):
    http_method_names = ["post"]

    def post(self, request, connection_id):
        connection = get_object_or_404(FacebookConnection, pk=connection_id, organization=self.organization)
        name = connection.facebook_user_name or "Facebook account"
        services.disconnect_connection(connection)
        messages.success(request, f"{name} was disconnected and its access tokens were deleted.")
        return redirect("facebook:index")


@method_decorator(csrf_exempt, name="dispatch")  # Meta can't send a CSRF token; the HMAC signature is the authentication.
class WebhookView(View):
    """Public endpoint called by Meta. Never trusts a body without a valid signature."""

    http_method_names = ["get", "post"]

    def get(self, request):
        challenge = services.verify_challenge(request.GET)
        if challenge is None:
            return HttpResponseForbidden("Verification failed")
        return HttpResponse(challenge, content_type="text/plain")

    def post(self, request):
        raw_body = request.body
        if not services.verify_signature(raw_body, request.headers.get("X-Hub-Signature-256", "")):
            return HttpResponseForbidden("Invalid signature")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return HttpResponseBadRequest("Invalid JSON")
        try:
            services.record_webhook_payload(payload)
        except Exception:  # never make Meta retry forever because of a bug on our side
            logger.exception("Failed to record Facebook webhook payload")
        return HttpResponse("EVENT_RECEIVED", content_type="text/plain")


@method_decorator(csrf_exempt, name="dispatch")  # called by Meta's servers; the signed_request is the authentication
class DeauthorizeView(View):
    """Meta calls this when someone removes Replytic from their Facebook settings."""

    http_method_names = ["post"]

    def post(self, request):
        payload = services.parse_signed_request(request.POST.get("signed_request", ""))
        if payload is None:
            return HttpResponseBadRequest("Invalid signed_request")
        services.forget_facebook_user(payload["user_id"])
        return HttpResponse(status=200)


@method_decorator(csrf_exempt, name="dispatch")
class DataDeletionView(View):
    """Meta calls this when a person asks Facebook to delete the data an app holds about them."""

    http_method_names = ["post"]

    def post(self, request):
        payload = services.parse_signed_request(request.POST.get("signed_request", ""))
        if payload is None:
            return HttpResponseBadRequest("Invalid signed_request")
        record = services.create_deletion_request(payload["user_id"])
        return JsonResponse({"url": services.deletion_status_url(record), "confirmation_code": record.confirmation_code})


class DeletionStatusView(TemplateView):
    """Public page Meta links people to so they can confirm their data was deleted."""

    template_name = "facebook/deletion_status.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        code = self.request.GET.get("code", "")
        context["record"] = FacebookDeletionRequest.objects.filter(confirmation_code=code).first() if code else None
        return context

from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.generic import TemplateView


class LandingView(TemplateView):
    template_name = "core/landing.html"

    FEATURES = [
        ("bi-lightning-charge", "Automatic replies", "Answer common questions about price, location and delivery the moment they're asked."),
        ("bi-person-check", "Lead capture", "Spot the people who are ready to buy and keep their details in one place."),
        ("bi-sliders", "Smart automation", "Set simple rules for what Replytic says, and when it stays quiet."),
        ("bi-person-raised-hand", "Human takeover", "Step in whenever a conversation needs a real person."),
        ("bi-bar-chart-line", "Analytics", "See how many comments turn into enquiries, leads and sales."),
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["features"] = self.FEATURES
        return context


def _error(request, status, template):
    return render(request, template, status=status)


def bad_request(request, exception=None):
    return _error(request, 400, "errors/400.html")


def permission_denied(request, exception=None):
    return _error(request, 403, "errors/403.html")


def page_not_found(request, exception=None):
    return _error(request, 404, "errors/404.html")


def server_error(request):
    # Rendered without a request context so a failure in a context processor
    # can never turn a 500 page into another 500.
    return HttpResponseServerError(render_to_string("errors/500.html"))

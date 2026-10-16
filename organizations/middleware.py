from .services import resolve_current_membership


class CurrentOrganizationMiddleware:
    """
    Attach ``request.membership`` and ``request.organization``.

    Runs after authentication and sessions. The organization is always
    re-verified against the database on every request, never trusted from the
    browser.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = None
        request.organization = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = resolve_current_membership(user, request.session)
            request.membership = membership
            request.organization = membership.organization if membership else None
        return self.get_response(request)

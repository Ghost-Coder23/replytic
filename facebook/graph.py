"""
Minimal Graph API client using only the standard library.

All network access goes through ``_open`` so tests can replace it. Access tokens
are sent in the request (POST body / query) and never logged.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_HOST = "https://graph.facebook.com"
DIALOG_HOST = "https://www.facebook.com"


class GraphAPIError(Exception):
    """Meta returned an error or was unreachable. ``str(error)`` is safe to show users."""

    def __init__(self, message, *, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def is_auth_error(self):
        return self.code in (190, 102, 10, 200) or self.status == 401


def _open(url, *, data=None, method="GET"):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=settings.META_HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as http_error:
        try:
            error = json.loads(http_error.read().decode()).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        raise GraphAPIError(
            error.get("message") or "Facebook rejected the request.",
            code=error.get("code"), status=http_error.code,
        ) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.warning("Could not reach the Facebook Graph API")
        raise GraphAPIError("We couldn't reach Facebook. Please try again in a moment.") from None


def _url(path):
    return f"{GRAPH_HOST}/{settings.META_GRAPH_VERSION}/{path.lstrip('/')}"


def get(path, **params):
    return _open(f"{_url(path)}?{urllib.parse.urlencode(params)}")


def post(path, **params):
    return _open(_url(path), data=params, method="POST")


def delete(path, **params):
    return _open(_url(path), data=params, method="DELETE")


def get_url(url):
    """Follow a full paging URL returned by Meta (must be on graph.facebook.com)."""
    if not url.startswith(GRAPH_HOST + "/"):
        raise GraphAPIError("Unexpected paging URL from Facebook.")
    return _open(url)


def login_dialog_url(*, state, redirect_uri):
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
    }
    if settings.META_LOGIN_CONFIG_ID:
        params["config_id"] = settings.META_LOGIN_CONFIG_ID
    else:
        params["scope"] = ",".join(settings.META_SCOPES)
    return f"{DIALOG_HOST}/{settings.META_GRAPH_VERSION}/dialog/oauth?{urllib.parse.urlencode(params)}"

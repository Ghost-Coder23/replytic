"""
Replytic settings (Phase 1: local development on SQLite).

Only SECRET_KEY, DEBUG and ALLOWED_HOSTS are read from the environment.
Values are read from real environment variables first, then from a ``.env``
file in the project root if one exists.
"""
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need an extra dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _env_bool("DEBUG", True)

SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("SECRET_KEY must be set when DEBUG is False.")
    # Development convenience only, so the app can start before anything else is set up.
    # This used to generate a new throw-away key on every restart, which seemed harmless
    # (it only signs sessions) but silently broke anything encrypted with it, including
    # stored Facebook access tokens, which became permanently unreadable after a restart
    # while the UI still showed the Page as "Connected". Persist the generated key into
    # .env instead, so it survives restarts. Set SECRET_KEY yourself for anything that
    # matters (deployments, or once real Facebook tokens are stored).
    _env_path = BASE_DIR / ".env"
    SECRET_KEY = get_random_secret_key()
    try:
        with _env_path.open("a", encoding="utf-8") as _f:
            _f.write(f"\nSECRET_KEY={SECRET_KEY}\n")
    except OSError:
        pass  # read-only filesystem, some containers: key just won't persist

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Replytic
    "core",
    "accounts",
    "organizations",
    "dashboard",
    "facebook",
    "automations",
    "comments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Resolves and verifies the current organization on every request.
    "organizations.middleware.CurrentOrganizationMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.workspace",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Users sign in with their email address (or username, e.g. for superusers).
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Harare"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# Password-reset emails are printed to the console in Phase 1.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "Replytic <no-reply@replytic.local>"

# ---------------------------------------------------------------------------
# Meta / Facebook integration (Phase 2). All optional: without them the
# Facebook page explains what is missing and nothing else is affected.
# ---------------------------------------------------------------------------
# Public base URL of this site. Meta redirects users back to
# SITE_URL + /facebook/callback/, so it must match a URI whitelisted in your Meta app.
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").strip().rstrip("/")

# Behind a tunnel or proxy (ngrok, Cloudflare) the browser talks HTTPS to SITE_URL. Trust that host
# and origin automatically so form posts aren't rejected by CSRF checks.
from urllib.parse import urlparse  # noqa: E402

_site = urlparse(SITE_URL)
if _site.hostname and _site.hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_site.hostname)
CSRF_TRUSTED_ORIGINS = [f"{_site.scheme}://{_site.netloc}"] if _site.netloc else []
META_APP_ID = os.environ.get("META_APP_ID", "").strip()
META_APP_SECRET = os.environ.get("META_APP_SECRET", "").strip()
META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "").strip()
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0").strip()
# Only for apps using "Facebook Login for Business"; leave empty for classic Facebook Login.
META_LOGIN_CONFIG_ID = os.environ.get("META_LOGIN_CONFIG_ID", "").strip()
# Connect Pages, read comments (pages_read_user_content) and reply (pages_manage_engagement).
META_SCOPES = [
    scope.strip()
    for scope in os.environ.get(
        "META_SCOPES",
        "pages_show_list,pages_manage_metadata,pages_read_engagement,"
        "pages_read_user_content,pages_manage_engagement",
    ).split(",")
    if scope.strip()
]
META_HTTP_TIMEOUT = 15

# Phase 3 safety limits
AUTO_REPLY_MAX_COMMENT_AGE_HOURS = 24  # older comments are never auto-answered, only drafted
MAX_REPLY_LENGTH = 1000

# Fernet key used to encrypt Facebook access tokens at rest.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
if not TOKEN_ENCRYPTION_KEY and not DEBUG:
    raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set when DEBUG is False.")

# Uploaded business logos.
LOGO_MAX_UPLOAD_BYTES = 2 * 1024 * 1024

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "info",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}

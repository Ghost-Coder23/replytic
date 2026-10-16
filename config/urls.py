from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include("accounts.urls")),
    path("", include("dashboard.urls")),
    path("", include("facebook.urls")),
    path("", include("comments.urls")),
    path("", include("automations.urls")),
    path("", include("organizations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = "core.views.bad_request"
handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"

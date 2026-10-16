from django.urls import path

from . import views

app_name = "facebook"

urlpatterns = [
    path("facebook/", views.FacebookIndexView.as_view(), name="index"),
    path("facebook/connect/", views.ConnectView.as_view(), name="connect"),
    path("facebook/callback/", views.CallbackView.as_view(), name="callback"),
    path("facebook/pages/refresh/", views.RefreshPagesView.as_view(), name="refresh_pages"),
    path("facebook/pages/<uuid:page_id>/connect/", views.ConnectPageView.as_view(), name="connect_page"),
    path("facebook/pages/<uuid:page_id>/disconnect/", views.DisconnectPageView.as_view(), name="disconnect_page"),
    path("facebook/accounts/<uuid:connection_id>/disconnect/", views.DisconnectAccountView.as_view(), name="disconnect_account"),
    path("webhooks/facebook/", views.WebhookView.as_view(), name="webhook"),
    path("facebook/deauthorize/", views.DeauthorizeView.as_view(), name="deauthorize"),
    path("facebook/data-deletion/", views.DataDeletionView.as_view(), name="data_deletion"),
    path("facebook/data-deletion/status/", views.DeletionStatusView.as_view(), name="deletion_status"),
]

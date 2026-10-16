from django.contrib import admin

from .models import FacebookConnection, FacebookDeletionRequest, FacebookPage, FacebookWebhookEvent


@admin.register(FacebookConnection)
class FacebookConnectionAdmin(admin.ModelAdmin):
    list_display = ("facebook_user_name", "organization", "status", "token_expires_at", "created_at")
    list_filter = ("status",)
    search_fields = ("facebook_user_name", "facebook_user_id", "organization__name")
    raw_id_fields = ("organization", "connected_by")
    exclude = ("access_token",)  # secrets are never shown in the admin


@admin.register(FacebookPage)
class FacebookPageAdmin(admin.ModelAdmin):
    list_display = ("name", "page_id", "organization", "status", "connected_at")
    list_filter = ("status",)
    search_fields = ("name", "page_id", "organization__name")
    raw_id_fields = ("organization", "connection")
    exclude = ("access_token",)


@admin.register(FacebookWebhookEvent)
class FacebookWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("field", "facebook_page_id", "organization", "received_at", "processed_at")
    list_filter = ("field",)
    search_fields = ("facebook_page_id", "organization__name")
    raw_id_fields = ("organization", "page")
    readonly_fields = ("payload", "dedupe_key", "received_at")


@admin.register(FacebookDeletionRequest)
class FacebookDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ("confirmation_code", "connections_removed", "created_at", "completed_at")
    readonly_fields = ("confirmation_code", "facebook_user_hash", "connections_removed", "created_at", "completed_at")

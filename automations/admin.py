from django.contrib import admin

from .models import AutomationRule


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "page", "action", "priority", "is_active", "match_count")
    list_filter = ("action", "is_active", "match_mode")
    search_fields = ("name", "keywords", "organization__name")
    raw_id_fields = ("organization", "page", "created_by")

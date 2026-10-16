from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from organizations.models import OrganizationMembership

User = get_user_model()


class UserMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    raw_id_fields = ("organization",)


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class ReplyticUserAdmin(UserAdmin):
    list_display = ("email", "username", "first_name", "last_name", "is_staff", "is_active", "organization_names", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active", "date_joined")
    search_fields = ("email", "username", "first_name", "last_name", "memberships__organization__name")
    inlines = [UserMembershipInline]

    @admin.display(description="Businesses")
    def organization_names(self, obj):
        return ", ".join(m.organization.name for m in obj.memberships.select_related("organization")[:3])

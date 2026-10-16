from django.contrib import admin

from .models import Organization, OrganizationMembership


class MembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    raw_id_fields = ["user"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "email", "timezone", "is_active", "member_count", "created_at")
    list_filter = ("is_active", "timezone", "created_at")
    search_fields = ("name", "slug", "email", "phone", "memberships__user__email")
    readonly_fields = ("id", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.memberships.count()


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = ("role", "organization", "created_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "organization__name")
    raw_id_fields = ("user", "organization")
    readonly_fields = ("id", "created_at", "updated_at")

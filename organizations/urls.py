from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("team/", views.TeamView.as_view(), name="team"),
    path("team/<uuid:membership_id>/role/", views.ChangeMemberRoleView.as_view(), name="change_role"),
    path("team/<uuid:membership_id>/remove/", views.RemoveMemberView.as_view(), name="remove_member"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("organizations/new/", views.CreateOrganizationView.as_view(), name="create"),
    path("organizations/switch/", views.SwitchOrganizationView.as_view(), name="switch"),
]

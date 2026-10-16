from django.urls import path

from . import views

app_name = "automations"

urlpatterns = [
    path("automations/", views.IndexView.as_view(), name="index"),
    path("automations/test/", views.TestRulesView.as_view(), name="test"),
    path("automations/new/", views.RuleCreateView.as_view(), name="create"),
    path("automations/<uuid:rule_id>/edit/", views.RuleUpdateView.as_view(), name="edit"),
    path("automations/<uuid:rule_id>/delete/", views.RuleDeleteView.as_view(), name="delete"),
    path("automations/<uuid:rule_id>/toggle/", views.RuleToggleView.as_view(), name="toggle"),
    path("automations/pages/<uuid:page_id>/mode/", views.PageModeView.as_view(), name="page_mode"),
]

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="index"),
    path("leads/", views.LeadsView.as_view(), name="leads"),
    path("knowledge/", views.KnowledgeView.as_view(), name="knowledge"),
    path("analytics/", views.AnalyticsView.as_view(), name="analytics"),
    path("billing/", views.BillingView.as_view(), name="billing"),
]

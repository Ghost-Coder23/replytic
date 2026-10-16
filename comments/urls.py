from django.urls import path

from . import views

app_name = "comments"

urlpatterns = [
    path("comments/", views.CommentsIndexView.as_view(), name="index"),
    path("comments/check/", views.CheckNewView.as_view(), name="check"),
    path("comments/<uuid:comment_id>/reply/", views.ReplyView.as_view(), name="reply"),
    path("comments/<uuid:comment_id>/retry/", views.RetryView.as_view(), name="retry"),
    path("comments/<uuid:comment_id>/ignore/", views.IgnoreView.as_view(), name="ignore"),
    path("comments/<uuid:comment_id>/reopen/", views.ReopenView.as_view(), name="reopen"),
    path("comments/<uuid:comment_id>/discard/", views.DiscardDraftView.as_view(), name="discard"),
]

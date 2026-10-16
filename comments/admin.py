from django.contrib import admin

from .models import Comment, Reply


class ReplyInline(admin.TabularInline):
    model = Reply
    extra = 0
    raw_id_fields = ("organization", "page", "rule", "created_by")
    readonly_fields = ("facebook_reply_id", "sent_at")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("author_name", "short_message", "organization", "page", "status", "commented_at")
    list_filter = ("status", "is_from_page", "is_deleted")
    search_fields = ("message", "author_name", "facebook_comment_id", "organization__name")
    raw_id_fields = ("organization", "page", "matched_rule", "handled_by")
    inlines = [ReplyInline]

    @admin.display(description="Comment")
    def short_message(self, obj):
        return obj.message[:60]


@admin.register(Reply)
class ReplyAdmin(admin.ModelAdmin):
    list_display = ("comment", "organization", "source", "status", "sent_at")
    list_filter = ("source", "status")
    search_fields = ("message", "organization__name")
    raw_id_fields = ("organization", "comment", "page", "rule", "created_by")

"""
Local, network-free health check for the Facebook integration.

Checks configuration, every connection's and Page's stored token, and how many
webhook events have ever arrived -- so a "comments aren't showing up" report can be
narrowed to "Replytic's side" or "Facebook's side" in seconds, without guessing.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection as db_connection

from facebook.models import FacebookConnection, FacebookPage, FacebookWebhookEvent


class Command(BaseCommand):
    help = "Check Facebook configuration, connections, Pages and webhook events without calling Facebook."

    def handle(self, *args, **options):
        ok = self.style.SUCCESS("OK")
        fail = self.style.ERROR("FAIL")

        self.stdout.write(self.style.MIGRATE_HEADING("Configuration"))
        checks = [
            ("META_APP_ID is set", bool(settings.META_APP_ID)),
            ("META_APP_SECRET is set", bool(settings.META_APP_SECRET)),
            ("META_WEBHOOK_VERIFY_TOKEN is set", bool(settings.META_WEBHOOK_VERIFY_TOKEN)),
            ("SITE_URL is a public https address", settings.SITE_URL.startswith("https://")),
        ]
        for label, passed in checks:
            self.stdout.write(f"{ok if passed else fail}  {label}")
        self.stdout.write(f"  SITE_URL = {settings.SITE_URL}")
        self.stdout.write(f"  Webhook callback should be: {settings.SITE_URL}/webhooks/facebook/\n")

        self.stdout.write(self.style.MIGRATE_HEADING("Connections"))
        connections = list(FacebookConnection.objects.select_related("organization"))
        if not connections:
            self.stdout.write("  (none yet -- nobody has clicked Connect Facebook)")
        for connection in connections:
            broken = self._ciphertext_present_but_undecryptable(
                "facebook_facebookconnection", connection.pk, bool(connection.access_token)
            )
            label = f"{connection.organization.name}: {connection.facebook_user_name or connection.facebook_user_id}"
            if connection.status != FacebookConnection.Status.ACTIVE:
                self.stdout.write(f"{fail}  {label} -- status is {connection.status}")
            elif broken:
                self.stdout.write(f"{fail}  {label} -- stored access token can't be decrypted")
                self.stdout.write("       This happens if TOKEN_ENCRYPTION_KEY (or the SECRET_KEY it falls back to)")
                self.stdout.write("       changed since this was connected. Reconnect Facebook to fix it.")
            else:
                self.stdout.write(f"{ok}  {label}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("Pages"))
        pages = list(FacebookPage.objects.select_related("organization"))
        if not pages:
            self.stdout.write("  (none yet)")
        for page in pages:
            label = f"{page.organization.name}: {page.name} ({page.page_id}) -- {page.status}, mode={page.automation_mode}"
            if page.status == FacebookPage.Status.CONNECTED:
                broken = self._ciphertext_present_but_undecryptable(
                    "facebook_facebookpage", page.pk, bool(page.access_token)
                )
                if broken:
                    self.stdout.write(f"{fail}  {label}")
                    self.stdout.write("       Shows 'Watching' but its access token can't be decrypted.")
                    self.stdout.write("       Reconnect Facebook, or click Stop watching then Watch this Page again.")
                elif page.last_error:
                    self.stdout.write(f"{fail}  {label} -- {page.last_error}")
                else:
                    self.stdout.write(f"{ok}  {label}")
            else:
                self.stdout.write(f"  {label}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("Webhook events received (ever, all businesses)"))
        total = FacebookWebhookEvent.objects.count()
        if total == 0:
            self.stdout.write(f"{fail}  Zero events have ever been stored.")
            self.stdout.write("       Facebook has never once called your webhook. This points to Facebook's side:")
            self.stdout.write("       check 'feed' is subscribed under Webhooks -> Page in your Meta app, and that")
            self.stdout.write("       whoever is commenting has a role on the app (required in Development mode).")
            self.stdout.write("       Run 'python manage.py send_test_webhook' to test Replytic's side on its own.")
        else:
            unprocessed = FacebookWebhookEvent.objects.filter(processed_at__isnull=True).count()
            latest = FacebookWebhookEvent.objects.order_by("-received_at").first()
            self.stdout.write(f"{ok}  {total} event(s) received. Most recent: {latest.received_at:%Y-%m-%d %H:%M:%S}")
            if unprocessed:
                self.stdout.write(f"{fail}  {unprocessed} event(s) are still waiting to be processed.")
                self.stdout.write("       Run 'python manage.py process_comments' (or keep --watch running).")

    @staticmethod
    def _ciphertext_present_but_undecryptable(table, pk, decrypted_value_is_truthy):
        """
        An encrypted field that decrypts to '' is ambiguous: it might genuinely be empty,
        or its ciphertext might exist but no longer be readable with the current key. Read
        the raw column directly (bypassing the field's automatic decryption) to tell them apart.

        The primary key must be passed through the database's own UUID adapter: SQLite
        stores UUIDField values as 32-character hex with no dashes, not str(uuid), so a
        plain string comparison silently matches nothing.
        """
        if decrypted_value_is_truthy:
            return False
        from django.db import models as django_models
        adapted_pk = django_models.UUIDField().get_db_prep_value(pk, db_connection)
        with db_connection.cursor() as cursor:
            cursor.execute(f"SELECT access_token FROM {table} WHERE id = %s", [adapted_pk])  # noqa: S608
            row = cursor.fetchone()
        return bool(row and row[0])

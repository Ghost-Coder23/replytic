import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from facebook.models import FacebookPage


class Command(BaseCommand):
    help = (
        "Send a correctly signed fake 'new comment' webhook to this server, exactly as Facebook would. "
        "Use it to tell whether a missing-comment problem is on Facebook's side or Replytic's."
    )

    def add_arguments(self, parser):
        parser.add_argument("--message", default="How much is this?", help="Comment text to simulate.")
        parser.add_argument("--author", default="Test Customer", help="Commenter name.")
        parser.add_argument("--page-id", help="Facebook Page id (default: first Page you are watching).")
        parser.add_argument("--url", default="http://127.0.0.1:8000/webhooks/facebook/", help="Where to send it.")

    def handle(self, *args, **options):
        if not settings.META_APP_SECRET:
            raise CommandError("META_APP_SECRET is not set in .env, so the event can't be signed.")
        page_id = options["page_id"]
        if not page_id:
            page = FacebookPage.objects.filter(status=FacebookPage.Status.CONNECTED).first()
            if page is None:
                raise CommandError("No Page is being watched yet. Click 'Watch this Page' first.")
            page_id = page.page_id
        stamp = int(time.time())
        payload = {"object": "page", "entry": [{"id": page_id, "time": stamp, "changes": [{"field": "feed", "value": {
            "item": "comment", "verb": "add", "comment_id": f"{page_id}_test{stamp}", "post_id": f"{page_id}_testpost",
            "parent_id": f"{page_id}_testpost", "message": options["message"], "created_time": stamp,
            "from": {"id": f"test-{stamp}", "name": options["author"]},
        }}]}]}
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(settings.META_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            options["url"], data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                self.stdout.write(self.style.SUCCESS(
                    f"Server answered {response.status}. Now run: python manage.py process_comments"
                ))
                self.stdout.write(self.style.WARNING(
                    "Note: this comment id is fake, so approving its suggested reply will fail when sent "
                    "for real -- that's expected. Discard it afterwards."
                ))
        except urllib.error.HTTPError as error:
            raise CommandError(
                f"Server answered {error.code}. 403 usually means META_APP_SECRET differs from the running server's."
            ) from None
        except urllib.error.URLError as error:
            raise CommandError(f"Couldn't reach {options['url']}: {error.reason}. Is runserver running?") from None

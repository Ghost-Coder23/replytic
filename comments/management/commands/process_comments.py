import time

from django.core.management.base import BaseCommand

from comments import services


class Command(BaseCommand):
    help = "Turn stored Facebook webhook events into comments, apply automation rules and send replies."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Keep running and check repeatedly.")
        parser.add_argument("--interval", type=int, default=5, help="Seconds between checks with --watch (default 5).")

    def handle(self, *args, **options):
        while True:
            recovered = services.recover_stuck_replies()
            handled = services.process_pending_events()
            if handled or recovered:
                self.stdout.write(f"Processed {handled} update(s); recovered {recovered} interrupted send(s).")
            if not options["watch"]:
                if not handled and not recovered:
                    self.stdout.write("Nothing to process.")
                return
            try:
                time.sleep(options["interval"])
            except KeyboardInterrupt:
                self.stdout.write("Stopped.")
                return

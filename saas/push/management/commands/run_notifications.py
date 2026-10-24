import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from push.service import process_notification_cycle
from billing.models import OwnerOperationsSettings


class Command(BaseCommand):
    help = "Run independent Push/outbox/daily-digest worker"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                result = process_notification_cycle()
            except Exception as error:
                # Delivery errors must not affect marketplace worker.
                self.stderr.write(f"Push worker cycle failed: {type(error).__name__}")
                if options["once"]:
                    raise
            else:
                if options["once"]:
                    self.stdout.write(str(result))
                    return
            if options["once"]:
                return
            policy = OwnerOperationsSettings.runtime()
            seconds = policy.notifications_poll_seconds if policy is not None else 30
            time.sleep(max(15, min(300, seconds)))

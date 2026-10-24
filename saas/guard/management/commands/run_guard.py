import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from guard.monitor import run_due_checks


class Command(BaseCommand):
    help = "Run the Wildberries guard worker (one instance per SaaS deployment)"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                count = run_due_checks()
            except Exception as error:
                self.stderr.write(f"WB guard cycle failed: {type(error).__name__}")
                if options["once"]:
                    raise
            else:
                if options["once"]:
                    self.stdout.write(f"Processed accounts: {count}")
                    return
            time.sleep(10)

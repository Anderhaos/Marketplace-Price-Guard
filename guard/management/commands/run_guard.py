import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from guard.monitor import run_due_checks


class Command(BaseCommand):
    help = "Run the Wildberries guard worker (one instance per SQLite database)"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            count = run_due_checks()
            if options["once"]:
                self.stdout.write(f"Processed accounts: {count}")
                return
            time.sleep(10)

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Enable SQLite WAL mode for the portal database"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
            mode = cursor.fetchone()[0]
        if mode.lower() != "wal":
            raise RuntimeError(f"Could not enable SQLite WAL: {mode}")
        self.stdout.write("SQLite WAL enabled")

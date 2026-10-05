import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create an online SQLite backup using the SQLite backup API"

    def add_arguments(self, parser):
        parser.add_argument("--directory", default="/backups")
        parser.add_argument("--keep", type=int, default=14)

    def handle(self, *args, **options):
        source_path = Path(settings.DATABASES["default"]["NAME"])
        if not source_path.is_file():
            raise RuntimeError(f"Database does not exist: {source_path}")
        directory = Path(options["directory"])
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = directory / f"portal-{stamp}.sqlite3"
        temporary = directory / f".{destination.name}.tmp"
        try:
            with sqlite3.connect(source_path) as source, sqlite3.connect(temporary) as target:
                source.backup(target)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        for old in sorted(directory.glob("portal-*.sqlite3"), reverse=True)[max(options["keep"], 1):]:
            old.unlink()
        self.stdout.write(f"Backup written: {destination}")

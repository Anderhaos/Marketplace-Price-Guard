"""Explicit owner promotion of a previously verified SaaS PIN account.

Never creates credentials, sends a PIN, takes a password, or elevates a guessed
address. This is a one-time trusted-operator maintenance command.
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Grant owner Django Admin rights to an existing account on a secure server allowlist"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Email of an already registered NoSale account")
        parser.add_argument("--confirm", action="store_true", help="Actually grant owner rights; omit to dry-run")

    def handle(self, *args, **options):
        if not settings.SAAS_PIN_AUTH_ENABLED or not settings.REQUIRE_EMAIL_VERIFICATION:
            raise CommandError("Owner promotion requires the confirmed email + PIN authentication flow")
        approved = os.environ.get("NOSALE_OWNER_EMAIL", "").strip().casefold()
        supplied = options["email"].strip().casefold()
        if not approved or "@" not in approved:
            raise CommandError("NOSALE_OWNER_EMAIL must be configured in the private SaaS environment")
        if supplied != approved:
            raise CommandError("Email does not match the privately configured NoSale owner")
        with transaction.atomic():
            users = list(
                get_user_model().objects.select_for_update().filter(email__iexact=approved)[:2]
            )
            if len(users) != 1:
                raise CommandError("Exactly one already registered owner account must exist")
            user = users[0]
            if not user.is_active or user.username.casefold() != approved:
                raise CommandError("Owner account must be active and registered with the same confirmed email")
            if not user.has_usable_password():
                raise CommandError("Owner account must have completed PIN setup")
            if user.is_staff and user.is_superuser:
                self.stdout.write("Owner access is already active; no changes needed.")
                return
            if not options["confirm"]:
                self.stdout.write("Owner account verified locally. Run with --confirm to grant access.")
                return
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
            self.stdout.write("Owner admin rights granted to the existing verified account.")

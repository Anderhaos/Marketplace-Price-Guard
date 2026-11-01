"""LOCAL-ONLY synthetic WB first-run server for actual headless-browser acceptance.

This module is not installed in the production web entrypoint. Run only in an
ephemeral Docker container with isolated SQLite + --network internal.
"""
import os
import sys
import time
import base64
import json
import threading
from unittest.mock import patch

if __name__ != "__main__" or os.environ.get("NOSALE_SYNTHETIC_BROWSER_E2E") != "1":
    raise SystemExit("Synthetic browser fixture requires explicit opt-in")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from billing.models import AccessGrant
from guard.models import GuardAccount, Product, PromotionSnapshot
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import path, clear_url_caches
from django.utils import timezone
from datetime import timedelta
from guard.stores import selected_store
import portal.urls
from guard.monitor import run_due_checks
from guard.wb import WBApiError

if not (
    settings.DEBUG and settings.SAAS_PIN_AUTH_ENABLED
    and settings.SAAS_WB_NETWORK_ENABLED and settings.SAAS_WB_READ_ONLY
    and settings.SAAS_ACCESS_ENFORCED
    and not settings.TOCHKA_BILLING_ENABLED
    and not settings.NOSALE_WB_FINANCE_ENABLED
    and settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
    and settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
):
    raise SystemExit("Unsafe E2E settings; refusing to run")

# Deny outbound access even on Docker bridge networking; a synthetic test
# server may connect only to loopback addresses for its own HTTP requests.
import socket
_original_connect = socket.socket.connect
def _fixture_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError("Synthetic NoSale browser fixture forbids outbound connections")
    return _original_connect(sock, address)
socket.socket.connect = _fixture_connect

settings.DATABASES["default"]["NAME"] = "/tmp/nosale-e2e-seller.sqlite3"
call_command("migrate", interactive=False, verbosity=0)


class SyntheticWBClient:
    def __init__(self, token):
        self.token = token

    def validate_token(self, probe_prices=True):
        return None

    def get_product_cards(self):
        # The extra JWT payload field exists ONLY in synthetic browser data.
        raw = self.token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if data.get("nosale_e2e_scenario") == "429":
            raise WBApiError("synthetic fixture cooldown", 429, retry_after=3600)
        return [
            {"nmID": 79001, "title": "Browser fixture item A",
             "photos": [{"big": "https://example.test/synthetic-a.jpg"}]},
            {"nmID": 79002, "title": "Browser fixture item B", "photos": []},
        ]

    def get_products(self):
        return [
            {"nmID": 79001, "vendorCode": "BROWSER-01", "discount": 25,
             "sizes": [{"price": 1200, "discountedPrice": 900}]},
            {"nmID": 79002, "vendorCode": "BROWSER-02", "discount": 10,
             "sizes": [{"price": 850, "discountedPrice": 765}]},
        ]

    def submit_discounts(self, values):
        raise AssertionError("E2E browser fixture must NEVER submit WB discounts")

    def get_upload_state(self, upload_id):
        raise AssertionError("E2E fixture must not reconcile WB writes")


@login_required
@require_POST
def seed_promotion_snapshot(request):
    """Isolated test-only seller fixture; never registered in production URLs."""
    account = selected_store(request)
    own = list(Product.objects.filter(account=account, active=True))
    if len(own) != 2 or account.marketplace != "wb":
        return JsonResponse({"error": "synthetic catalog not ready"}, status=409)
    now = timezone.now()
    PromotionSnapshot.objects.update_or_create(account=account, defaults={
        "promotions": [
            {
                "id": 711, "name": "Синтетическая автоакция WB", "type": "auto",
                "starts_at": (now-timedelta(days=1)).isoformat(),
                "ends_at": (now+timedelta(days=8)).isoformat(),
                "seller_counts": {"inPromoActionTotal": 1, "exceptionProductsCount": 0},
                "participation": None, "risk": "unknown", "funding": "unknown",
            },
            {
                "id": 712, "name": "Синтетическая обычная акция WB", "type": "regular",
                "starts_at": (now+timedelta(days=1)).isoformat(),
                "ends_at": (now+timedelta(days=6)).isoformat(),
                "seller_counts": {"inPromoActionTotal": 1},
                "participation": {
                    "inspected": 1, "matched_count": 1, "matched_decreases": 1,
                    "matched": [{
                        "nm_id": 79001, "title": "Browser fixture item A", "vendor_code": "BROWSER-01",
                        "current": "900.00", "planned": "750.00", "decrease": True,
                        "current_discount": 25, "allowed_discount": 13,
                    }],
                },
                "risk": "price_drop", "funding": "unknown",
            },
            {
                "id": 713, "name": "Синтетическая акция без подтверждения", "type": "auto",
                "starts_at": (now+timedelta(days=3)).isoformat(),
                "ends_at": (now+timedelta(days=9)).isoformat(),
                "participation": None, "seller_counts": None,
                "risk": "unknown", "funding": "unknown",
            },
        ],
        "partial": True,
        "fetched_at": now,
        "next_refresh_at": now+timedelta(minutes=65),
    })
    return JsonResponse({"ok": True, "campaigns": 3})


# Test-only route exists solely in this ephemeral, no-egress fixture process.
portal.urls.urlpatterns.insert(0, path("__e2e/seed-promotions/", seed_promotion_snapshot))
clear_url_caches()


def serve():
    host = "0.0.0.0" if os.environ.get("NOSALE_E2E_DOCKER_BRIDGE") == "1" else "127.0.0.1"
    call_command("runserver", host + ":8941", use_reloader=False, verbosity=0)


with patch("guard.integrations.WBClient", SyntheticWBClient), patch(
    "guard.monitor.WBClient", SyntheticWBClient
), patch("guard.email_pin.secrets.randbelow", return_value=7):
    print("NOSALE_E2E_FIXTURE_READY http://0.0.0.0:8941", flush=True)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    while True:
        try:
            count = run_due_checks()
            if count:
                print("NOSALE_E2E_SYNTHETIC_SYNC processed=" + str(count),
                      "stores=" + str(GuardAccount.objects.count()),
                      "products=" + str(Product.objects.count()),
                      "trial_grants=" + str(AccessGrant.objects.filter(source="trial").count()),
                      flush=True)
        except Exception as error:
            print("NOSALE_E2E_WORKER_FAILURE type=" + type(error).__name__, file=sys.stderr, flush=True)
            raise
        time.sleep(1)

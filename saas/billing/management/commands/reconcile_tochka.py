"""Authenticated bank-status reconciliation for missed signed webhooks."""
import logging
import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from billing.models import SubscriptionPayment
from billing.service import reconcile_payment
from billing.tochka import AcquiringUnavailable, InvalidWebhook, configured
from guard.worker_health import mark_success

log = logging.getLogger(__name__)


def reconcile_due_payments():
    if not configured():
        return 0
    now = timezone.now()
    pending = SubscriptionPayment.objects.filter(
        status__in=("pending", "failed"), created_at__gt=now - timedelta(days=30),
    ).exclude(operation_id="").filter(
        Q(last_reconciled_at__isnull=True) | Q(last_reconciled_at__lte=now - timedelta(minutes=30))
    ).order_by("last_reconciled_at", "id")[:25]
    paid = SubscriptionPayment.objects.filter(
        status="paid", paid_at__gt=now - timedelta(days=365),
    ).exclude(operation_id="").filter(
        Q(last_reconciled_at__isnull=True) | Q(last_reconciled_at__lte=now - timedelta(days=1))
    ).order_by("last_reconciled_at", "id")[:25]
    ids = [p.pk for p in pending] + [p.pk for p in paid]
    checked = 0
    for pk in ids:
        payment = SubscriptionPayment.objects.get(pk=pk)
        try:
            result = reconcile_payment(payment)
        except (AcquiringUnavailable, InvalidWebhook):
            log.warning("nosale_reconcile_rejected payment=%s", pk)
        except Exception:
            log.exception("nosale_reconcile_failed payment=%s", pk)
        else:
            SubscriptionPayment.objects.filter(pk=pk).update(last_reconciled_at=timezone.now())
            if result == "partial_refund_manual_review":
                log.error("nosale_partial_refund_manual_review payment=%s", pk)
            checked += 1
    return checked


class Command(BaseCommand):
    help = "Reconcile Tochka operations, without charging users"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                result = reconcile_due_payments()
                mark_success("reconciliation")
                if options["once"]:
                    self.stdout.write(f"Reconciled: {result}")
                    return
            except Exception:
                log.exception("nosale_reconcile_cycle_failed")
                if options["once"]:
                    raise
            time.sleep(600)

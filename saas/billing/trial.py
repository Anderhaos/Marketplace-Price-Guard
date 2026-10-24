"""Five-day, once-per-account trial using the canonical AccessGrant ledger.

Same access-grant pattern as MiniBook. Trial starts on the first successfully
validated marketplace integration, not on initial email confirmation.
"""
from datetime import timedelta
from math import ceil

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from .models import AccessGrant

TRIAL_DAYS = 5


def trial_source_ref(user_id):
    return f"nosale:trial:user:{int(user_id)}"


def grant_trial_once(user_id, *, at=None):
    now = at or timezone.now()
    # Idempotent, including across workers and reconnects or secondary stores.
    # Never update an existing grant's end date.
    grant, created = AccessGrant.objects.get_or_create(
        source_ref=trial_source_ref(user_id),
        defaults=dict(
            user_id=user_id,
            source="trial",
            starts_at=now,
            ends_at=now + timedelta(days=TRIAL_DAYS),
        ),
    )
    if grant.user_id != user_id or grant.source != "trial":
        raise ValueError("Trial source_ref collision")
    return grant, created


def next_paid_period_start(user_id, *, at=None):
    """Earliest new paid period under the deferred-upgrade policy.

    Include already scheduled future grants; ignore revoked or expired ones.
    Payment approval serializes on the user before using this same rule.
    """
    now = at or timezone.now()
    latest_end = AccessGrant.objects.filter(
        user_id=user_id, revoked_at__isnull=True, ends_at__gt=now,
    ).order_by("-ends_at").values_list("ends_at", flat=True).first()
    return max(now, latest_end) if latest_end else now


def access_snapshot(user_id, *, at=None):
    now = at or timezone.now()
    active = AccessGrant.objects.filter(
        user_id=user_id, starts_at__lte=now, ends_at__gt=now,
        revoked_at__isnull=True,
    ).order_by(
        Case(When(plan="smart", then=Value(0)), default=Value(1), output_field=IntegerField()),
        "-ends_at",
    ).first()
    upcoming = AccessGrant.objects.filter(
        user_id=user_id, source="payment", starts_at__gt=now,
        revoked_at__isnull=True,
    ).order_by("starts_at").first()
    trial = AccessGrant.objects.filter(source_ref=trial_source_ref(user_id)).first()
    return {
        "active": bool(active),
        "access_source": active.source if active else None,
        "active_plan": active.plan if active else None,
        "upcoming_plan": upcoming.plan if upcoming else None,
        "upcoming_starts_at": upcoming.starts_at if upcoming else None,
        "next_purchase_starts_at": next_paid_period_start(user_id, at=now),
        "expires_at": active.ends_at if active else None,
        "days_left": max(0, ceil((active.ends_at - now).total_seconds() / 86400)) if active else 0,
        "trial_started": bool(trial),
        "trial_ends_at": trial.ends_at if trial else None,
        "trial_expired": bool(trial and trial.ends_at <= now and not active),
        "trial_days": TRIAL_DAYS,
    }

from django.conf import settings
from django.utils import timezone
from django.db.models import Case, IntegerField, Value, When
from .models import AccessGrant, OwnerTariffOffer


# Inspired by BeClients billing-capabilities.ts: one effective AccessGrant
# selects a small set of server-enforced features. This is intentionally
# not a second subscription/payment ledger.
# Actual working features, not conceptual/retired product claims. Owner can
# combine these independently per tariff; unimplemented AI chat is not listed.
FEATURE_CHOICES = (
    ("discount_guard", "Автоматическая защита скидок WB"),
    ("notifications", "Уведомления, отчёты и Web Push"),
    ("activity_history", "История проверок и событий"),
    ("promotion_audit", "Аудит акций Wildberries"),
    ("finance_audit", "Финансовая аналитика Wildberries"),
    ("store_advice", "Smart: рекомендации по расписанию"),
    ("ai_advice", "DeepSeek: краткое AI-резюме рекомендаций"),
)
FEATURE_KEYS = frozenset(code for code, _ in FEATURE_CHOICES)
FEATURE_DESCRIPTIONS = {
    "discount_guard": "Автоматическая проверка и исправление скидок в пределах настроенных правил.",
    "notifications": "События, ежедневные отчёты и отправка Push на устройства.",
    "activity_history": "Хронология проверок, изменений и конкретных товаров.",
    "promotion_audit": "Календарь акций WB, предупреждения и обновление данных по запросу.",
    "finance_audit": "Финансовые отчёты WB с отдельным согласием и токеном.",
    "store_advice": "Скриптовые выводы по данным магазинов, лента и плановые сводки.",
    "ai_advice": "Одна короткая DeepSeek-формулировка только при согласии и наличии бюджета.",
}
# Compatibility when a tariff row has not yet been configured by the owner.
# NULL means legacy defaults; an explicitly saved [] means no features.
PLAN_CAPABILITIES = {
    "standard": frozenset({
        "discount_guard", "notifications", "activity_history",
        "promotion_audit", "finance_audit", "web_assistant_basic",
    }),
    "smart": FEATURE_KEYS | frozenset({"web_assistant_basic", "ai_questions"}),
}


def capabilities_for_plan(plan):
    default = PLAN_CAPABILITIES.get(plan, frozenset())
    if not default:
        return frozenset()
    configured = OwnerTariffOffer.objects.filter(slug=plan).values_list(
        "features", flat=True,
    ).first()
    if configured is None:
        return default
    if not isinstance(configured, list):
        return frozenset()
    return frozenset(code for code in configured if isinstance(code, str) and code in FEATURE_KEYS)


def capabilities_for_user(user_id, at=None):
    plan = effective_plan(user_id, at=at)
    return capabilities_for_plan(plan)


def feature_disabled_for_active_plan(user_id, capability, at=None):
    plan = effective_plan(user_id, at=at)
    return plan is not None and capability not in capabilities_for_plan(plan)



def effective_plan(user_id, at=None):
    """No active grant means no paid capabilities; Smart wins overlapping grants."""
    now = at or timezone.now()
    return AccessGrant.objects.filter(
        user_id=user_id, starts_at__lte=now, ends_at__gt=now, revoked_at__isnull=True,
    ).order_by(
        Case(When(plan="smart", then=Value(0)), default=Value(1), output_field=IntegerField()),
        "-ends_at",
    ).values_list("plan", flat=True).first()


def has_plan_capability(user_id, capability, at=None):
    if not isinstance(capability, str) or not capability:
        return False
    return capability in capabilities_for_user(user_id, at=at)


def has_active_access(user_id, at=None):
    if not settings.SAAS_ACCESS_ENFORCED:
        return True
    now = at or timezone.now()
    return AccessGrant.objects.filter(
        user_id=user_id, starts_at__lte=now, ends_at__gt=now,
        revoked_at__isnull=True,
    ).exists()

def guard_is_permitted(account):
    return (settings.SAAS_WB_NETWORK_ENABLED
            and has_active_access(account.user_id)
            and (not settings.SAAS_ACCESS_ENFORCED
                 or has_plan_capability(account.user_id, "discount_guard")))


def first_inventory_import_permitted(account):
    """One safe first read before the trial starts; no expired-trial reset.

    Any prior subscription or trial requires the normal active-access gate.
    Never allow pending price writes to exploit onboarding access.
    """
    if not (settings.SAAS_PIN_AUTH_ENABLED and settings.SAAS_ACCESS_ENFORCED
            and settings.SAAS_WB_NETWORK_ENABLED):
        return False
    if (account.initial_sync_completed_at is not None or not account.check_requested
            or account.enabled or account.apply_all_requested or account.apply_requested_ids):
        return False
    if AccessGrant.objects.filter(user_id=account.user_id).exists():
        return False
    from guard.models import Upload
    return not Upload.objects.filter(account_id=account.pk, status="pending").exists()


def has_smart_access(user_id, at=None):
    """Compatibility alias: privileged capability always checked server-side."""
    return has_plan_capability(user_id, "store_advice", at=at)

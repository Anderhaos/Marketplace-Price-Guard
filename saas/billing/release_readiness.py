"""Read-only and fail-closed release evidence for NoSale.

No provider I/O, writes, secret printing or automatic launch approvals.
External attestations are recorded in GitHub issue #12 and cannot be inferred
from local flags. Smart contract #11 is scheduled advice, NOT a free AI chat.
"""
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from guard.models import GuardAccount, Product, Upload
from .models import (
    OwnerAISettings, OwnerAIGatewayConnection, OwnerTariffOffer,
    PaymentRefundAttempt, SubscriptionPayment,
)

REQUIRED_EXTERNAL_EVIDENCE = (
    ("wb_saas_permission", "Официальное разрешение на коммерческий SaaS WB API"),
    ("seller_onboarding", "Реальный новый продавец: email/PIN, собственный WB, trial, isolation"),
    ("bank_card", "Оплата картой обоих тарифов, чек, сумма, AccessGrant"),
    ("bank_sbp", "Оплата СБП обоих тарифов, чек, сумма, AccessGrant"),
    ("owner_refund", "Возврат через админку, чек возврата, отзыв AccessGrant"),
    ("legal", "Оферта, privacy, WB и согласие на AI утверждены ответственным лицом"),
    ("offsite_backup", "Успешное восстановление из внешнего зашифрованного бэкапа"),
    ("ci_and_review", "CI Django/PostgreSQL/Chromium по финальному SHA, review, rollback"),
)

def _item(code, ok, evidence):
    return {"id": code, "ok": bool(ok), "evidence": evidence}

def release_report(*, now=None):
    now = now or timezone.now()
    User = get_user_model()
    owners = User.objects.filter(is_active=True, is_staff=True, is_superuser=True).count()
    sellers = User.objects.filter(is_active=True, is_superuser=False).count()
    secure = all((
        not settings.DEBUG, settings.SESSION_COOKIE_SECURE,
        settings.CSRF_COOKIE_SECURE, settings.SECURE_SSL_REDIRECT,
        settings.SAAS_ACCESS_ENFORCED, settings.SAAS_PIN_AUTH_ENABLED,
        settings.REQUIRE_EMAIL_VERIFICATION,
    ))
    active_stores = GuardAccount.objects.filter(enabled=True, marketplace="wb")
    recent = list(active_stores.filter(
        last_check_at__gte=now - timedelta(hours=24),
        last_error="", token_ciphertext__gt="",
    ).values_list("pk", flat=True)[:100])
    verified_inventory = sum(
        Product.objects.filter(account_id=pk, active=True).exists()
        and not Upload.objects.filter(account_id=pk, status="pending").exists()
        for pk in recent
    )
    wb_ok = bool(settings.SAAS_WB_NETWORK_ENABLED and not settings.SAAS_WB_READ_ONLY
                 and verified_inventory)
    ai = OwnerAISettings.objects.filter(pk=1).first()
    gw = OwnerAIGatewayConnection.objects.filter(pk=1).first()
    smart_ok = bool(
        ai and ai.ready_for_gateway and ai.ai_platform_cost_cap_confirmed
        and gw and gw.configured and gw.last_check_ok is True
        and ai.gateway_model_id in (gw.catalog_models or [])
        and ai.smart_briefs_per_day in (0, 1, 2)
    )
    offers = {p["slug"]: p for p in OwnerTariffOffer.objects.values(
        "slug", "enabled", "price_kopeks",
    )}
    offers_ok = all(
        offers.get(slug, {}).get("enabled", False)
        and offers[slug]["price_kopeks"] >= 100
        for slug in ("standard", "smart")
    )
    bank_ok = bool(settings.TOCHKA_BILLING_ENABLED and settings.NOSALE_FISCALIZATION_CONFIRMED)
    refund_audits = PaymentRefundAttempt.objects.filter(status="confirmed").count()
    paid_count = SubscriptionPayment.objects.filter(status="paid").count()
    refunded_count = SubscriptionPayment.objects.filter(status="refunded").count()
    checks = [
        _item("security", secure, "Secure cookies, HTTPS, email/PIN, grant enforcement"),
        _item("owner", owners == 1, f"active_owners={owners}; active_sellers={sellers}"),
        _item("wb", wb_ok, f"active_stores={active_stores.count()}; recent_verified_inventory={verified_inventory}"),
        _item("smart_scheduled", smart_ok, "Verified project/model, cost cap and scheduled AI"),
        _item("bank_fiscal", bank_ok, f"configured={bank_ok}; paid_rows={paid_count}; refunded_rows={refunded_count}"),
        _item("offers", offers_ok, "Standard and Smart approved, enabled, priced on server"),
        _item("admin_refund", refund_audits > 0, f"confirmed_admin_refund_attempts={refund_audits}"),
    ]
    beta_check_ids = {"security", "owner", "wb", "smart_scheduled"}
    return {
        "as_of": now.isoformat(),
        "scope": "Standard WB guard + Smart scheduled DeepSeek briefs; no free AI chat",
        "checks": checks,
        "controlled_beta_technically_ready": all(
            c["ok"] for c in checks if c["id"] in beta_check_ids
        ),
        "external_evidence": [
            {"id": key, "requirement": label, "verified_locally": False}
            for key, label in REQUIRED_EXTERNAL_EVIDENCE
        ],
        # No local config can ever stand in for independent commercial signoff.
        "public_go": False,
        "decision": "NO_GO_EXTERNAL_ACCEPTANCE_REQUIRED"
                    if all(c["ok"] for c in checks)
                    else "NO_GO_TECHNICAL_AND_EXTERNAL",
    }

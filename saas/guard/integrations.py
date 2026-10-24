"""Safe, one-time WB API onboarding and token replacement for SaaS sellers."""
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from billing.trial import grant_trial_once
from .crypto import encrypt_token
from .models import GuardAccount, GuardEvent, Upload
from .wb import WBApiError, WBClient, get_token_permissions, get_token_type


class ConnectError(Exception):
    pass


def connect_wb_token(account, token):
    token = (token or "").strip()
    if not token or len(token) > 4096:
        raise ConnectError("Вставьте полный API-токен Wildberries.")
    if not getattr(settings, "SAAS_WB_TOKEN_VALIDATION_ENABLED", False):
        raise ConnectError("Проверка WB API пока недоступна. Попробуйте позже.")
    permissions = get_token_permissions(token)
    if not permissions:
        raise ConnectError("Не удалось определить права токена. Создайте новый токен WB.")
    if permissions["token_type"] == "personal":
        raise ConnectError("Персональный токен нельзя использовать в облачном сервисе. Используйте сервисный токен WB или временный базовый токен.")
    if permissions["token_type"] == "test":
        raise ConnectError("Тестовый токен работает только в песочнице WB. Нужен токен реального магазина.")
    if permissions["token_type"] not in ("service", "base"):
        raise ConnectError("Неизвестный тип токена WB. Создайте сервисный или базовый токен.")
    required = {"Цены и скидки", "Контент"}
    if not required.issubset(set(permissions["categories"])):
        raise ConnectError("Добавьте категории «Цены и скидки» и «Контент» при создании токена.")
    if permissions["token_type"] != "base" and permissions["access"] != "Чтение и запись":
        raise ConnectError("Для сервисного токена выберите «Чтение и запись». Базовый токен поддерживает просмотр.")

    # First validate read-only: never send discount changes during onboarding.
    try:
        if permissions["token_type"] == "base":
            WBClient(token).validate_token(probe_prices=False)
        else:
            WBClient(token).validate_token()
    except WBApiError as error:
        if error.status == 401:
            raise ConnectError("WB отклонил токен. Проверьте срок действия и скопируйте его полностью.") from error
        if error.status == 403:
            raise ConnectError("WB отказал в доступе. Проверьте категории и права токена.") from error
        raise ConnectError("Не удалось проверить WB API. Повторите попытку позже.") from error
    ciphertext = encrypt_token(token)
    with transaction.atomic():
        current = GuardAccount.objects.select_for_update().get(pk=account.pk, user_id=account.user_id)
        if current.token_ciphertext:
            pending = Upload.objects.filter(account=current, status="pending").update(
                status="unknown", details="API-токен изменён до получения ответа WB."
            )
            if pending:
                GuardEvent.objects.create(
                    account=current, kind="upload_unknown",
                    message="Ожидавшие подтверждения WB операции требуют проверки после смены API.",
                )
        current.token_ciphertext = ciphertext
        current.token_hint = token[:4] + "…" + token[-4:] if len(token) >= 12 else "сохранён"
        current.enabled = False  # users explicitly opt in to automatic corrections
        # Never replay pending price writes against a newly connected store.
        current.apply_all_requested = False
        current.apply_requested_ids = []
        current.check_requested = True  # read-only first refresh, if enabled
        current.lock_until = None
        current.rules_version += 1  # fences active workers on stale credentials/rules
        current.last_error = ""
        current.next_check_at = timezone.now()
        current.save(update_fields=[
            "token_ciphertext", "token_hint", "enabled", "apply_all_requested",
            "apply_requested_ids", "check_requested", "lock_until",
            "rules_version", "last_error", "next_check_at",
        ])
        grant, created = grant_trial_once(current.user_id)
    return permissions, grant, created

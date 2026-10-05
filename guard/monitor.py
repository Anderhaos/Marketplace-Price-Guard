from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from .crypto import decrypt_token
from .models import GuardAccount, GuardEvent, Product, Upload
from .wb import WBApiError, WBClient


def _event(account, kind, message, nm_id=None):
    GuardEvent.objects.create(account=account, kind=kind, nm_id=nm_id, message=message[:2000])


def _price(size, key):
    value = size.get(key)
    if value is None:
        return None


def _card_data(client):
    data = {}
    for card in client.get_product_cards():
        nm_id = card.get("nmID")
        if not nm_id:
            continue
        photos = card.get("photos") or []
        photo = photos[0] if photos else {}
        data[int(nm_id)] = {
            "title": str(card.get("title") or "")[:500],
            "photo_url": str(photo.get("big") or photo.get("c246x328") or ""),
        }
    return data


def _save_card_data(account, card_data):
    for nm_id, card in card_data.items():
        Product.objects.filter(account=account, nm_id=nm_id).update(**card)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _reconcile_uploads(account, client):
    pending = Upload.objects.filter(account=account, status="pending").order_by("created_at")
    still_pending = False
    successful_upload = False
    for upload in pending:
        status = client.get_upload_state(upload.upload_id)
        upload.checked_at = timezone.now()
        if status == 1:
            still_pending = True
            upload.save(update_fields=["checked_at"])
            continue
        if status == 3:
            upload.status = "success"
            successful_upload = True
            upload.details = f"Подтверждено WB: {len(upload.product_ids)} товаров"
            _event(account, "confirmed", f"WB подтвердил изменение скидок, загрузка {upload.upload_id}: {len(upload.product_ids)} товаров.")
        elif status in {4, 5, 6}:
            upload.status = "failed" if status in {4, 6} else "partial"
            errors = []
            if status in {5, 6}:
                for item in client.get_upload_details(upload.upload_id):
                    if item.get("errorText"):
                        errors.append(f"{item.get('nmID')}: {item['errorText']}")
            upload.details = "\n".join(errors)[:4000] or f"WB status: {status}"
            _event(account, "upload_error", f"Загрузка {upload.upload_id}: {upload.details}")
        else:
            still_pending = True
            upload.save(update_fields=["checked_at"])
            continue
        upload.save(update_fields=["status", "details", "checked_at"])
    return still_pending, successful_upload


def check_account(account):
    token_ciphertext = account.token_ciphertext
    rules_version = account.rules_version
    client = WBClient(decrypt_token(token_ciphertext))
    still_pending, successful_upload = _reconcile_uploads(account, client)
    if still_pending:
        return True
    account.refresh_from_db(fields=["enabled", "check_requested", "apply_all_requested", "apply_requested_ids", "default_allowed_discount"])
    force_apply_all = account.apply_all_requested
    force_apply_ids = {int(nm_id) for nm_id in account.apply_requested_ids}
    if not account.enabled and not account.check_requested and not force_apply_all and not force_apply_ids and not successful_upload:
        return False
    card_data = {}
    try:
        card_data = _card_data(client)
        _save_card_data(account, card_data)
    except WBApiError:
        pass
    goods = client.get_products()
    checked_at = timezone.now()
    changes = []
    corrected_count = 0
    problems = 0
    for item in goods:
        nm_id = int(item["nmID"])
        sizes = item.get("sizes") or []
        first_size = sizes[0] if sizes else {}
        discount = int(item.get("discount") or 0)
        previous = Product.objects.filter(account=account, nm_id=nm_id).first()
        product, _ = Product.objects.update_or_create(
            account=account, nm_id=nm_id,
            defaults={
                "vendor_code": str(item.get("vendorCode") or "")[:255],
                "title": card_data.get(nm_id, {}).get("title", previous.title if previous else ""),
                "photo_url": card_data.get(nm_id, {}).get("photo_url", previous.photo_url if previous else ""),
                "price": _price(first_size, "price"),
                "discounted_price": _price(first_size, "discountedPrice"),
                "currency": str(item.get("currencyIsoCode4217") or "RUB")[:3],
                "current_discount": discount,
                "checked_at": checked_at,
                "active": True,
            },
        )
        if previous:
            if previous.current_discount != discount:
                _event(account, "discount_changed", f"Скидка в WB: {previous.current_discount}% → {discount}%.", nm_id)
            if previous.price != product.price:
                _event(account, "price_changed", f"Цена первого размера: {previous.price or '—'} → {product.price or '—'} {product.currency}.", nm_id)
        allowed = product.allowed_discount_override
        if allowed is None:
            allowed = account.default_allowed_discount
        if force_apply_all or nm_id in force_apply_ids:
            changes.append((nm_id, account.default_allowed_discount))
            if nm_id in force_apply_ids and product.allowed_discount_override is not None:
                changes[-1] = (nm_id, product.allowed_discount_override)
            if discount != changes[-1][1]:
                corrected_count += 1
        elif account.enabled and discount > allowed:
            problems += 1
            if len(changes) < account.max_fixes:
                changes.append((nm_id, allowed))
                corrected_count += 1
    Product.objects.filter(account=account).exclude(checked_at=checked_at).update(active=False)
    _event(account, "check", f"Проверено товаров: {len(goods)}; скидка выше разрешённой: {problems}.")
    if not changes or (not account.enabled and not force_apply_all and not force_apply_ids):
        return False
    account.refresh_from_db(fields=["enabled", "apply_all_requested", "apply_requested_ids", "token_ciphertext", "rules_version"])
    if (not account.enabled and not account.apply_all_requested and not account.apply_requested_ids) or account.token_ciphertext != token_ciphertext or account.rules_version != rules_version:
        _event(account, "paused", "Настройки подключения изменились во время проверки; изменения не отправлены.")
        return False
    upload_id = client.submit_discounts(changes)
    upload, created = Upload.objects.get_or_create(
        account=account, upload_id=upload_id,
        defaults={"product_ids": [nm_id for nm_id, _ in changes], "corrected_count": corrected_count},
    )
    if not created:
        upload.status = "pending"
        upload.product_ids = [nm_id for nm_id, _ in changes]
        upload.corrected_count = corrected_count
        upload.save(update_fields=["status", "product_ids", "corrected_count"])
    if force_apply_all:
        _event(account, "submitted", f"Отправлена задача WB {upload_id}: установить скидку {account.default_allowed_discount}% для {len(changes)} товаров. Ожидается подтверждение.")
    else:
        _event(account, "submitted", f"Отправлена задача WB {upload_id}: {len(changes)} товаров. Ожидается подтверждение.")
    return True


def run_due_checks():
    now = timezone.now()
    due = GuardAccount.objects.filter(token_ciphertext__gt="", next_check_at__lte=now).filter(Q(enabled=True) | Q(check_requested=True) | Q(apply_all_requested=True) | ~Q(apply_requested_ids=[]) | Q(uploads__status="pending")).filter(Q(lock_until__isnull=True) | Q(lock_until__lte=now)).distinct().order_by("next_check_at")[:20]
    processed = 0
    for account in due:
        claimed = GuardAccount.objects.filter(pk=account.pk, next_check_at__lte=now).filter(Q(enabled=True) | Q(check_requested=True) | Q(apply_all_requested=True) | ~Q(apply_requested_ids=[]) | Q(uploads__status="pending")).filter(Q(lock_until__isnull=True) | Q(lock_until__lte=now)).update(lock_until=now + timedelta(minutes=15))
        if not claimed:
            continue
        processed += 1
        try:
            pending = check_account(account)
            delay = timedelta(minutes=1 if pending else account.interval_minutes)
            GuardAccount.objects.filter(pk=account.pk).update(
                lock_until=None, check_requested=False, apply_all_requested=False, apply_requested_ids=[], last_check_at=timezone.now(), last_error="",
                next_check_at=timezone.now() + delay,
            )
        except Exception as error:
            if isinstance(error, WBApiError) and error.status == 429:
                message = "Wildberries временно ограничил запросы. Повторная попытка будет выполнена позже."
                delay = timedelta(seconds=max(error.retry_after or 60, 60))
            else:
                message = "Не удалось обновить данные Wildberries. Повторная попытка будет выполнена позже."
                delay = timedelta(minutes=5)
            _event(account, "error", message)
            GuardAccount.objects.filter(pk=account.pk).update(
                lock_until=None, check_requested=False, apply_all_requested=False, apply_requested_ids=[], last_check_at=timezone.now(), last_error=message,
                next_check_at=timezone.now() + delay,
            )
    return processed

from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.db.models.functions import TruncDate
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.conf import settings

from billing.access import guard_is_permitted
from billing.trial import access_snapshot
from .integrations import connect_wb_token, ConnectError
from .crypto import decrypt_token, encrypt_token
from .forms import AccountSettingsForm, DefaultDiscountForm, ProductRuleForm, RegistrationForm, TokenForm, StoreCreateForm
from .models import GuardAccount, GuardEvent, Product, Upload
from .stores import selected_store, SESSION_KEY
from push.models import NotificationPreference, PushMessage, PushSubscription
from push.service import SUPPORTED_TIMEZONES
from .monitor import check_account
from .wb import WBClient, WBApiError, get_token_permissions, get_token_type


def health(request):
    return HttpResponse("ok", content_type="text/plain")


def favicon(request):
    response = FileResponse((settings.BASE_DIR / "portal" / "static" / "favicon.svg").open("rb"), content_type="image/svg+xml")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def pwa_manifest(request):
    response = FileResponse(
        (settings.BASE_DIR / "portal" / "static" / "manifest.webmanifest").open("rb"),
        content_type="application/manifest+json",
    )
    response["Cache-Control"] = "public, max-age=3600"
    return response


def pwa_service_worker(request):
    response = FileResponse(
        (settings.BASE_DIR / "portal" / "static" / "sw.js").open("rb"),
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            if settings.REQUIRE_EMAIL_VERIFICATION:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                link = request.build_absolute_uri(f"/verify/{uid}/{token}/")
                send_mail("Подтвердите регистрацию MP No Sale", f"Перейдите по ссылке для активации аккаунта:\n{link}", settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
            else:
                user.is_active = True
                user.save(update_fields=["is_active"])
        if settings.REQUIRE_EMAIL_VERIFICATION:
            return render(request, "guard/message.html", {"notice": "Проверьте почту и подтвердите регистрацию."})
        messages.success(request, "Аккаунт создан. Войдите, чтобы подключить Wildberries.")
        return redirect("login")
    return render(request, "guard/register.html", {"form": form, "email_verification_required": settings.REQUIRE_EMAIL_VERIFICATION})


def password_reset_unavailable(request):
    return render(request, "guard/message.html", {"notice": "Восстановление пароля через email временно недоступно. Обратитесь к администратору портала."}, status=503)


def verify(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        user = None
    if user and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, "Аккаунт подтверждён. Теперь войдите.")
        return redirect("login")
    return render(request, "guard/message.html", {"notice": "Ссылка подтверждения недействительна."}, status=400)


def _account(request):
    return selected_store(request)


def _account_token_type(account):
    if not account.token_ciphertext:
        return None
    try:
        return get_token_type(decrypt_token(account.token_ciphertext))
    except (ValueError, TypeError):
        return None


def _check_now(account):
    """Never talk to WB without an active grant and explicit network approval."""
    if not guard_is_permitted(account):
        return "no_access"
    if _account_token_type(account) == "base":
        return "base_token"
    now = timezone.now()
    claimed = GuardAccount.objects.filter(pk=account.pk).filter(
        Q(lock_until__isnull=True) | Q(lock_until__lte=now),
    ).update(lock_until=now + timedelta(minutes=15), check_requested=True)
    if not claimed:
        return None
    account.refresh_from_db()
    rules_version = account.rules_version
    lease_until = now + timedelta(minutes=15)
    try:
        outcome = check_account(account)
    except Exception as error:
        delay = timedelta(minutes=account.interval_minutes)
        if isinstance(error, WBApiError) and error.status == 429:
            if error.retry_after:
                delay = max(delay, timedelta(seconds=error.retry_after + 5))
            message = f"WB ограничил запрос {error}. Повтор через {int(delay.total_seconds() // 60)} мин."
        else:
            message = f"Не удалось обновить данные WB: {type(error).__name__}."
        GuardAccount.objects.filter(pk=account.pk).update(
            lock_until=None, check_requested=True, last_check_at=now,
            last_error=message, next_check_at=now + delay,
        )
        return False
    updates = dict(lock_until=None, last_check_at=now, last_error="",
                   next_check_at=now + timedelta(minutes=account.interval_minutes))
    if outcome in ("waiting_upload", "remaining"):
        updates["check_requested"] = True
    else:
        updates.update(check_requested=False, apply_all_requested=False, apply_requested_ids=[])
    updated = GuardAccount.objects.filter(
        pk=account.pk, lock_until=lease_until, rules_version=rules_version,
    ).update(**updates)
    if not updated:
        GuardAccount.objects.filter(pk=account.pk, lock_until=lease_until).update(
            lock_until=None, next_check_at=timezone.now()
        )
    return outcome if outcome in ("waiting_upload", "remaining") else True


_DASHBOARD_FILTERS = frozenset({"all", "over_limit", "corrected"})


def _dashboard_filter_name(request):
    value = request.POST.get("dashboard_filter") or request.GET.get("filter", "all")
    return value if value in _DASHBOARD_FILTERS else "all"


def _dashboard_redirect(request):
    filter_name = _dashboard_filter_name(request)
    url = reverse("dashboard")
    if filter_name != "all":
        url = f"{url}?filter={filter_name}"
    return redirect(url)


def _corrected_nm_ids(account):
    ids = set()
    for upload in Upload.objects.filter(account=account, status="success"):
        for nm_id in upload.product_ids or []:
            try:
                ids.add(int(nm_id))
            except (TypeError, ValueError):
                continue
    return ids


def _filter_active_products(active_products, filter_name, corrected_ids):
    if filter_name == "over_limit":
        return [product for product in active_products if product.current_discount > product.allowed_discount]
    if filter_name == "corrected":
        return [product for product in active_products if product.nm_id in corrected_ids]
    return active_products


@login_required
def stores(request):
    """Manage WB store slots. Ozon stays disabled until adapter and auth exist."""
    if request.method == "POST":
        form = StoreCreateForm(request.POST)
        if form.is_valid():
            account = GuardAccount.objects.create(
                user=request.user, marketplace="wb", name=form.cleaned_data["name"]
            )
            request.session[SESSION_KEY] = account.pk
            messages.success(request, "Магазин создан. Подключение API пока выключено.")
            return redirect("stores")
    else:
        form = StoreCreateForm()
    return render(request, "guard/stores.html", {
        "form": form,
        "stores": GuardAccount.objects.filter(user=request.user).order_by("id"),
        "selected_store_id": selected_store(request).id,
    })


@login_required
def select_store(request, store_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    store = get_object_or_404(GuardAccount, user=request.user, pk=store_id)
    request.session[SESSION_KEY] = store.pk
    messages.success(request, f"Выбран магазин: {store.name}")
    return redirect("dashboard")


@login_required
def onboarding_connect(request):
    """Allow token replacement only while the initial inventory is pending."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    account = _account(request)
    if account.initial_sync_completed_at is not None:
        return redirect("settings")
    return render(request, "guard/onboarding_start.html", {
        "onboarding_required": True,
        "trial_status": access_snapshot(request.user.pk),
    })


@login_required
def connect_wb(request):
    """First-run popup and settings share the same secure token verifier."""
    if request.method != "POST":
        return redirect("dashboard")
    account = _account(request)
    form = TokenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Введите API-токен Wildberries.")
        return redirect("dashboard")
    try:
        permissions, grant, started = connect_wb_token(account, form.cleaned_data["token"])
    except ConnectError as error:
        messages.error(request, str(error))
        return redirect("dashboard")
    if started:
        messages.success(request, "API проверен. Началась первичная загрузка товаров. Ваши 5 бесплатных дней начались.")
    elif access_snapshot(request.user.pk)["active"]:
        messages.success(request, "Токен проверен и сохранён.")
    else:
        messages.info(request, "Токен сохранён. Пробный период уже завершён — для мониторинга нужен активный доступ.")
    if permissions["token_type"] == "base":
        messages.info(request, "Базовый токен WB ограничен: для автоматической защиты нужен сервисный токен.")
    return redirect("dashboard")


@login_required
def dashboard(request):
    account = _account(request)
    if settings.SAAS_PIN_AUTH_ENABLED:
        if not account.token_ciphertext:
            if request.method != "GET":
                return HttpResponseNotAllowed(["GET"])
            return render(request, "guard/onboarding_start.html", {
                "onboarding_required": True,
                "trial_status": access_snapshot(request.user.pk),
            })
        if account.initial_sync_completed_at is None:
            if request.method == "POST":
                if request.POST.get("action") != "retry_initial_sync":
                    return HttpResponseNotAllowed(["GET"])
                if not settings.SAAS_WB_NETWORK_ENABLED:
                    messages.error(request, "В тестовой среде запросы WB отключены. Реальная синхронизация недоступна.")
                elif account.lock_until and account.lock_until > timezone.now():
                    messages.info(request, "Синхронизация уже выполняется.")
                elif account.next_check_at > timezone.now() and not account.last_error:
                    messages.info(request, "Загрузка уже запланирована.")
                else:
                    GuardAccount.objects.filter(pk=account.pk, user_id=request.user.pk).update(
                        check_requested=True, last_error="", next_check_at=timezone.now(), lock_until=None,
                    )
                    messages.info(request, "Повторная загрузка поставлена в очередь.")
                return redirect("dashboard")
            return render(request, "guard/onboarding_sync.html", {
                "account": account, "onboarding_required": True,
                "trial_status": access_snapshot(request.user.pk),
                "wb_sync_available": settings.SAAS_WB_NETWORK_ENABLED,
            })
    base_token = _account_token_type(account) == "base"
    # GET never performs network I/O: the dedicated worker refreshes WB data.
    if request.method == "POST":
        action = request.POST.get("action", "apply_all")
        if settings.SAAS_WB_READ_ONLY and action in ("toggle_autocontrol", "apply_all"):
            messages.error(request, "В тестовом режиме изменения цен и скидок Wildberries отключены.")
            return _dashboard_redirect(request)
        if action == "toggle_autocontrol":
            if not guard_is_permitted(account):
                messages.error(request, "Для запуска защиты нужна активная подписка и подключённый сервис.")
            elif not account.token_ciphertext:
                messages.error(request, "Сначала подключите токен Wildberries.")
            elif request.POST.get("enabled") == "on" and base_token:
                messages.error(request, "Для автоисправления скидок нужен токен с подходящими лимитами WB. Проверьте варианты подключения в настройках.")
            elif (request.POST.get("enabled") == "on" and account.default_allowed_discount == 0
                  and request.POST.get("confirm_zero_discount") != "1"):
                messages.error(request, "Подтвердите отключение всех скидок перед включением защиты.")
            else:
                enabled = request.POST.get("enabled") == "on"
                GuardAccount.objects.filter(pk=account.pk).update(enabled=enabled, next_check_at=timezone.now())
                messages.success(request, "Автоисправление включено." if enabled else "Автоисправление выключено.")
            return _dashboard_redirect(request)
        if action == "apply_all":
            if base_token:
                messages.error(request, "Базовый токен поддерживает только просмотр. Для изменения скидок нужен сервисный API.")
                return _dashboard_redirect(request)
            form = DefaultDiscountForm(request.POST)
            if form.is_valid():
                discount = form.cleaned_data["default_allowed_discount"]
                if discount == 0 and request.POST.get("confirm_zero_discount") != "1":
                    messages.error(request, "Для ограничения скидок до 0% требуется подтверждение.")
                    return _dashboard_redirect(request)
                if settings.SAAS_PIN_AUTH_ENABLED and not guard_is_permitted(account):
                    messages.error(request, "Для применения правил требуется действующий пробный или оплаченный доступ.")
                    return _dashboard_redirect(request)
                if not account.token_ciphertext:
                    messages.error(request, "Сначала подключите токен Wildberries.")
                    return redirect("settings")
                with transaction.atomic():
                    GuardAccount.objects.filter(pk=account.pk).update(
                        default_allowed_discount=discount, apply_all_requested=True,
                        next_check_at=timezone.now(), rules_version=F("rules_version") + 1,
                    )
                result = _check_now(account)
                if result is True:
                    messages.success(request, f"Максимум {discount}% сохранён. Превышения отправлены в Wildberries.")
                elif result is False:
                    messages.error(request, f"Максимум {discount}% сохранён. WB пока не ответил; превышения будут проверены в следующий интервал.")
                elif result == "base_token":
                    messages.error(request, f"Максимум {discount}% сохранён. Для отправки изменений нужен токен с подходящими лимитами WB. Подробности в настройках.")
                else:
                    messages.success(request, f"Максимальная скидка {discount}% сохранена; текущая проверка отправит её в Wildberries.")
            else:
                messages.error(request, "Введите скидку от 0 до 99%.")
            return _dashboard_redirect(request)
        return HttpResponseNotAllowed(["GET", "POST"])
    active_products = list(Product.objects.filter(account=account, active=True).select_related("account"))
    data_checked_at = max((product.checked_at for product in active_products if product.checked_at), default=None)
    corrected_ids = _corrected_nm_ids(account)
    filter_name = _dashboard_filter_name(request)
    filtered_products = _filter_active_products(active_products, filter_name, corrected_ids)
    products = filtered_products[:500]
    corrected_count = Upload.objects.filter(account=account, status="success").aggregate(total=Sum("corrected_count"))["total"] or 0
    pending_product_ids = {int(value) for value in account.apply_requested_ids}
    for upload in Upload.objects.filter(account=account, status="pending"):
        pending_product_ids.update(int(value) for value in upload.product_ids)
    over_limit_count = sum(1 for product in active_products if product.current_discount > product.allowed_discount)
    filter_labels = {
        "all": "Все товары под контролем",
        "over_limit": "Выше установленного предела",
        "corrected": "Отменённые скидки (подтверждено Wildberries)",
    }
    return render(request, "guard/dashboard.html", {
        "account": account, "products": products, "count": len(active_products),
        "data_checked_at": data_checked_at, "base_token": base_token,
        "shown_count": len(filtered_products), "filter_name": filter_name, "filter_label": filter_labels[filter_name],
        "corrected_count": corrected_count, "corrected_card_count": len(corrected_ids), "over_limit_count": over_limit_count,
        "pending_product_ids": pending_product_ids,
        "product_cards_missing": bool(active_products) and not any(product.title or product.photo_url for product in active_products),
        "refresh_error": "",
        "onboarding_required": settings.SAAS_PIN_AUTH_ENABLED and not bool(account.token_ciphertext),
        "wb_read_only": settings.SAAS_WB_READ_ONLY,
        "wb_token_form": TokenForm(),
        "trial_status": access_snapshot(request.user.pk),
    })


@login_required
def settings_view(request):
    account = _account(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "token":
            token_form = TokenForm(request.POST)
            settings_form = AccountSettingsForm(instance=account)
            if token_form.is_valid():
                try:
                    permissions, grant, created = connect_wb_token(account, token_form.cleaned_data["token"])
                    messages.success(request, "Токен проверен и сохранён.")
                    if created:
                        messages.success(request, "Начался пробный период: 5 дней.")
                    return redirect("settings")
                except ConnectError as error:
                    token_form.add_error("token", str(error))
        elif action == "settings":
            settings_form = AccountSettingsForm(request.POST, instance=account)
            if settings_form.is_valid():
                updated = settings_form.save(commit=False)
                updated.next_check_at = timezone.now()
                updated.save()
                GuardAccount.objects.filter(pk=account.pk).update(rules_version=F("rules_version") + 1)
                messages.success(request, "Настройки сохранены.")
                return redirect("settings")
            token_form = TokenForm()
        elif action == "check":
            if not account.token_ciphertext:
                messages.error(request, "Сначала подключите токен Wildberries.")
                return redirect("settings")
            result = _check_now(account)
            if result is True:
                messages.success(request, "Данные Wildberries обновлены.")
            elif result is False:
                messages.error(request, "Wildberries пока не ответил. Проверка поставлена в очередь до следующего интервала.")
            elif result == "base_token":
                messages.error(request, "Базовый токен WB не подходит для автоисправления с текущей частотой. Проверьте варианты подключения в настройках.")
            else:
                messages.success(request, "Проверка уже выполняется.")
            return redirect("dashboard")
        else:
            return HttpResponseNotAllowed(["GET"])
    else:
        token_form = TokenForm()
        settings_form = AccountSettingsForm(instance=account)
    token_permissions = None
    if account.token_ciphertext:
        try:
            token_permissions = get_token_permissions(decrypt_token(account.token_ciphertext))
        except (ValueError, TypeError):
            # The token itself is never rendered or logged if it cannot be read.
            pass
    return render(request, "guard/settings.html", {
        "account": account,
        "token_form": token_form,
        "settings_form": settings_form,
        "token_permissions": token_permissions,
    })


@login_required
def product_rule(request, nm_id):
    account = _account(request)
    product = get_object_or_404(Product, account=account, nm_id=nm_id, active=True)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if settings.SAAS_WB_READ_ONLY:
        messages.error(request, "В тестовом режиме изменение скидок Wildberries отключено.")
        return _dashboard_redirect(request)
    if _account_token_type(account) == "base":
        messages.error(request, "Базовый токен WB разрешает только просмотр товаров.")
        return _dashboard_redirect(request)
    if settings.SAAS_PIN_AUTH_ENABLED and not guard_is_permitted(account):
        messages.error(request, "Пробный период закончился или защита WB временно недоступна.")
        return _dashboard_redirect(request)
    if request.POST.get("no_discount") == "on":
        product.allowed_discount_override = 0
        product.save(update_fields=["allowed_discount_override"])
        message = "Для товара включён режим «Без скидки»: гвард будет возвращать скидку к 0%."
    else:
        form = ProductRuleForm(request.POST)
        if form.is_valid():
            product.allowed_discount_override = form.cleaned_data["allowed_discount"]
            product.save(update_fields=["allowed_discount_override"])
            if product.allowed_discount_override is None:
                message = "Индивидуальный предел удалён; действует общий предел скидки."
            else:
                message = f"Максимальная скидка для товара: {product.allowed_discount_override}%."
        else:
            messages.error(request, "Скидка должна быть от 0 до 99%.")
            return _dashboard_redirect(request)
    with transaction.atomic():
        locked = GuardAccount.objects.select_for_update().get(pk=account.pk)
        requested_ids = {int(value) for value in locked.apply_requested_ids}
        requested_ids.add(product.nm_id)
        locked.apply_requested_ids = sorted(requested_ids)
        locked.rules_version = F("rules_version") + 1
        locked.next_check_at = timezone.now()
        locked.save(update_fields=["apply_requested_ids", "rules_version", "next_check_at"])
    GuardEvent.objects.create(account=account, kind="rule", nm_id=nm_id, message=message)
    if account.token_ciphertext:
        result = _check_now(account)
        if result is True:
            messages.success(request, f"{message} Изменение отправлено в Wildberries.")
        elif result is False:
            messages.error(request, f"{message} Wildberries пока не ответил; изменение поставлено в очередь до следующего интервала.")
        elif result == "base_token":
            messages.error(request, f"{message} Для отправки скидки нужен токен с подходящими лимитами WB. Подробности в настройках.")
        else:
            messages.success(request, f"{message} Проверка уже выполняется; правило будет применено в её ходе.")
    else:
        messages.success(request, f"{message} Подключите токен, чтобы отправить изменение в Wildberries.")
    return _dashboard_redirect(request)


@login_required
def product_history_data(request, nm_id):
    account = _account(request)
    product = get_object_or_404(Product, account=account, nm_id=nm_id)
    events = GuardEvent.objects.filter(account=account, nm_id=nm_id)[:100]
    return JsonResponse({
        "title": product.title or product.vendor_code or f"Товар WB {product.nm_id}",
        "subtitle": f"Артикул {product.vendor_code or '—'} · WB {product.nm_id}",
        "events": [{
            "time": event.created_at.strftime("%d.%m.%Y %H:%M:%S"),
            "kind": event.kind,
            "message": event.message,
        } for event in events],
    })


IMPORTANT_EVENT_KINDS = ("confirmed", "upload_error", "error")


@login_required
def notifications(request):
    events = GuardEvent.objects.filter(
        account__user=request.user,
        kind__in=IMPORTANT_EVENT_KINDS,
    ).select_related("account").order_by("-created_at", "-id")[:100]
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    digest_messages = PushMessage.objects.filter(user=request.user, kind="digest").order_by("-created_at")[:30]
    return render(request, "guard/notifications.html", {
        "events": events, "digest_messages": digest_messages,
        "preferences": pref, "push_public_key": settings.PUSH_VAPID_PUBLIC_KEY,
        "push_ready": bool(settings.PUSH_VAPID_PUBLIC_KEY and settings.PUSH_VAPID_PRIVATE_KEY),
        "push_devices": PushSubscription.objects.filter(user=request.user, active=True).count(),
        "timezones": sorted(SUPPORTED_TIMEZONES),
    })


@login_required
def mark_notifications_read(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    GuardEvent.objects.filter(
        account__user=request.user,
        kind__in=IMPORTANT_EVENT_KINDS,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    PushMessage.objects.filter(
        user=request.user, kind="digest", read_at__isnull=True,
    ).update(read_at=timezone.now())
    return redirect("notifications")


@login_required
def history(request):
    account = _account(request)
    nm_id = request.GET.get("product")
    events = GuardEvent.objects.filter(account=account)
    product = None
    if nm_id:
        try:
            product = Product.objects.get(account=account, nm_id=int(nm_id))
        except (Product.DoesNotExist, ValueError):
            product = None
        else:
            events = events.filter(nm_id=product.nm_id)

    days = {}
    def ensure_day(day):
        if day not in days:
            days[day] = {"date": day, "checks": 0, "events": [], "confirmed": 0, "errors": 0}
        return days[day]

    # Aggregate routine checks in SQL instead of loading every technical entry.
    recent = timezone.now() - timedelta(days=30)
    checks = events.filter(kind="check", created_at__gte=recent).annotate(
        day=TruncDate("created_at")
    ).values("day").annotate(count=Count("id")).order_by("-day")[:31]
    for row in checks:
        ensure_day(row["day"])["checks"] = row["count"]

    for event in events.exclude(kind="check").order_by("-created_at", "-id")[:250]:
        day = timezone.localtime(event.created_at).date()
        entry = ensure_day(day)
        entry["events"].append(event)
        if event.kind == "confirmed":
            entry["confirmed"] += len(event.details.get("changes", [])) or 1
        elif event.kind in ("error", "upload_error"):
            entry["errors"] += 1
    ordered_days = sorted(days.values(), key=lambda day: day["date"], reverse=True)
    return render(request, "guard/history.html", {"days": ordered_days, "product": product})

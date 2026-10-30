import re
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

from billing.access import guard_is_permitted, has_smart_access, has_plan_capability
from .advisor import store_recommendations
from .assistant import basic_assistant_reply
from .ai_gateway import AIGatewayUnavailable
from .smart_assistant import smart_store_reply
from .ai_safety import contains_credential
from billing.trial import access_snapshot
from billing.models import OwnerAISettings
from .integrations import connect_wb_token, ConnectError
from .crypto import decrypt_token, encrypt_token
from .forms import AccountSettingsForm, DefaultDiscountForm, ProductRuleForm, RegistrationForm, TokenForm, StoreCreateForm
from .models import AssistantMessage, GuardAccount, GuardEvent, Product, Upload
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


def _account_can_write(account):
    if not account.token_ciphertext:
        return False
    try:
        info = get_token_permissions(decrypt_token(account.token_ciphertext))
    except (ValueError, TypeError):
        # Historical legacy portal tests and pre-SaaS deployments may store
        # opaque credentials; do not change their old non-PIN behavior.
        return not settings.SAAS_PIN_AUTH_ENABLED
    if info is None:
        return not settings.SAAS_PIN_AUTH_ENABLED
    return info["access"] == "Чтение и запись"


def _check_now(account):
    """Never talk to WB without an active grant and explicit network approval."""
    if not guard_is_permitted(account):
        return "no_access"
    if not _account_can_write(account):
        return "read_only_token"
    # Basic tokens allow writes, but have much stricter WB API limits.
    # Queue corrections for the existing worker instead of issuing a burst
    # of price requests synchronously from the user's browser.
    if _account_token_type(account) == "base":
        now = timezone.now()
        min_due = ((account.last_check_at + timedelta(minutes=60))
                   if account.last_check_at else now)
        GuardAccount.objects.filter(pk=account.pk).update(
            check_requested=True, next_check_at=max(now, min_due),
        )
        return "queued_basic"
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
            messages.success(request, "Добавлен отдельный кабинет WB. Для загрузки товаров потребуется его собственный API-токен.")
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
        "wb_connect_available": bool(settings.SAAS_WB_NETWORK_ENABLED and settings.SAAS_WB_TOKEN_VALIDATION_ENABLED),
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
    trial = access_snapshot(request.user.pk)
    if trial["trial_expired"]:
        messages.info(request, "Токен сохранён, но пробный период уже завершён. Для мониторинга нужен активный доступ.")
    elif not trial["trial_started"] and not trial["active"]:
        messages.success(request, "API проверен. Загружаем товары; 5 бесплатных дней начнутся после успешной первой загрузки.")
    else:
        messages.success(request, "Токен проверен и сохранён. Повторное подключение не продлевает пробный период.")
    if permissions["token_type"] == "base":
        messages.info(request, "Базовый токен с правом записи подключён. Из-за лимитов WB проверка и исправление скидок будут выполняться реже. Для частой защиты нужен сервисный токен.")
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
                "wb_connect_available": bool(settings.SAAS_WB_NETWORK_ENABLED and settings.SAAS_WB_TOKEN_VALIDATION_ENABLED),
            })
        if account.initial_sync_completed_at is None:
            if request.method == "POST":
                if request.POST.get("action") != "retry_initial_sync":
                    return HttpResponseNotAllowed(["GET"])
                if not settings.SAAS_WB_NETWORK_ENABLED:
                    messages.error(request, "В тестовой среде запросы WB отключены. Реальная синхронизация недоступна.")
                elif account.lock_until and account.lock_until > timezone.now():
                    messages.info(request, "Синхронизация уже выполняется.")
                elif account.next_check_at > timezone.now():
                    # HTTP 429 must never be bypassed just because an error was
                    # recorded. The WB token limit applies to the seller,
                    # including manual retries and concurrent sessions.
                    messages.info(request, "WB ограничил частоту запросов. Повторная загрузка уже запланирована.")
                else:
                    now = timezone.now()
                    # Atomic guard against two browser tabs racing to requeue.
                    queued = GuardAccount.objects.filter(
                        pk=account.pk, user_id=request.user.pk,
                        next_check_at__lte=now,
                    ).filter(
                        Q(lock_until__isnull=True) | Q(lock_until__lte=now)
                    ).update(
                        check_requested=True, last_error="",
                        next_check_at=now, lock_until=None,
                    )
                    messages.info(
                        request,
                        "Загрузка поставлена в очередь." if queued
                        else "Загрузка уже выполняется или ожидает разрешённого времени.",
                    )
                return redirect("dashboard")
            return render(request, "guard/onboarding_sync.html", {
                "account": account, "onboarding_required": True,
                "trial_status": access_snapshot(request.user.pk),
                "wb_sync_available": settings.SAAS_WB_NETWORK_ENABLED,
                "retry_waiting": bool(
                    account.last_error and account.next_check_at > timezone.now()
                ),
                "rate_limited": (
                    "429" in account.last_error
                    and account.next_check_at > timezone.now()
                ),
            })
    token_readonly = not _account_can_write(account)
    # GET never performs network I/O: the dedicated worker refreshes WB data.
    if request.method == "POST":
        action = request.POST.get("action", "apply_all")
        if settings.SAAS_WB_READ_ONLY and action in ("toggle_autocontrol", "apply_all"):
            messages.error(request, "В тестовом режиме изменения цен и скидок Wildberries отключены.")
            return _dashboard_redirect(request)
        if action == "save_limit":
            # Local policy editing never queues WB writes.
            if settings.SAAS_PIN_AUTH_ENABLED and not guard_is_permitted(account):
                messages.error(request, "Для изменения правил требуется действующий доступ.")
                return _dashboard_redirect(request)
            form = DefaultDiscountForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Введите скидку от 0 до 99%.")
                return _dashboard_redirect(request)
            discount = form.cleaned_data["default_allowed_discount"]
            if discount == 0 and request.POST.get("confirm_zero_discount") != "1":
                messages.error(request, "Подтвердите нулевой предел скидки.")
                return _dashboard_redirect(request)
            GuardAccount.objects.filter(pk=account.pk, user_id=request.user.pk).update(
                default_allowed_discount=discount, rules_version=F("rules_version") + 1,
            )
            messages.success(request, f"Предел {discount}% сохранён только в NoSale. Wildberries не изменялся.")
            return _dashboard_redirect(request)
        if action == "toggle_autocontrol":
            if not guard_is_permitted(account):
                messages.error(request, "Для запуска защиты нужна активная подписка и подключённый сервис.")
            elif not account.token_ciphertext:
                messages.error(request, "Сначала подключите токен Wildberries.")
            elif request.POST.get("enabled") == "on" and token_readonly:
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
            if token_readonly:
                messages.error(request, "Для изменения скидок требуется токен с уровнем «Чтение и запись».")
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
                elif result == "queued_basic":
                    messages.info(request, f"Максимум {discount}% сохранён. Изменения поставлены в очередь с учётом лимитов базового токена WB.")
                elif result == "read_only_token":
                    messages.error(request, "Для изменения скидок необходим токен с уровнем «Чтение и запись».")
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
        "data_checked_at": data_checked_at, "token_readonly": token_readonly,
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


def _web_assistant_policy():
    """Web chat policy: owner-configurable, independent of AI availability."""
    owner = OwnerAISettings.objects.filter(pk=1).values(
        "web_assistant_enabled", "web_messages_per_day",
    ).first()
    if owner is None:
        return True, 80  # preserve pre-owner configuration behavior
    return bool(owner["web_assistant_enabled"]), max(1, min(1000, owner["web_messages_per_day"]))


def _assistant_message_limit_reached(user_id, now, *, daily_limit):
    """One bounded message quota shared by Standard and Smart/fallback modes."""
    messages = AssistantMessage.objects.filter(user_id=user_id)
    recent = messages.filter(created_at__gte=now - timedelta(minutes=1)).count()
    daily = messages.filter(created_at__gte=now - timedelta(hours=24)).count()
    return recent >= 8 or daily >= daily_limit


@login_required
def assistant_chat(request):
    """Standard assistant lives in NoSale; no Telegram and no marketplace I/O."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    if not has_plan_capability(request.user.pk, "web_assistant_basic"):
        return render(request, "guard/assistant_access.html", {
            "access_title": "Помощник пока недоступен",
            "access_explanation": "Базовый помощник включён в действующий пробный период или подписку NoSale."
        }, status=403)

    web_enabled, daily_limit = _web_assistant_policy()
    if not web_enabled:
        return render(request, "guard/assistant_access.html", {
            "access_title": "Веб-помощник временно отключён",
            "access_explanation": "Владелец сервиса приостановил чат. Контроль скидок работает независимо.",
        }, status=503)

    account = _account(request)  # exclusively selected via current user's stores
    if request.method == "POST":
        question = request.POST.get("question", "")
        mode = request.POST.get("mode", "basic")
        if not isinstance(question, str) or not question.strip() or len(question) > 400:
            return HttpResponse("Введите вопрос длиной от 1 до 400 символов.", status=400)
        # Do not persist pasted JWT-like marketplace tokens in chat history.
        if contains_credential(question):
            return HttpResponse("Не отправляйте ключи WB или API-токены в помощник.", status=400)
        if mode not in ("basic", "smart"):
            return HttpResponse("Неизвестный режим помощника.", status=400)
        if mode == "smart" and not has_smart_access(request.user.pk):
            return render(request, "guard/assistant_access.html", {
                "access_title": "Smart недоступен в вашем тарифе",
                "access_explanation": "Для AI-вопросов по магазину нужна действующая подписка Smart."
            }, status=403)
        if mode == "smart" and request.POST.get("ai_consent") != "yes":
            return HttpResponse("Подтвердите отправку вопроса и обезличенных данных в AI.", status=400)

        if mode == "smart":
            # Check before provider I/O to avoid spending AI budget for a
            # request already over the shared web-chat message quota.
            with transaction.atomic():
                User.objects.select_for_update().get(pk=request.user.pk)
                if _assistant_message_limit_reached(request.user.pk, timezone.now(), daily_limit=daily_limit):
                    return HttpResponse("Слишком много вопросов. Повторите немного позже.", status=429)
            # Provider I/O never runs inside a DB transaction or with a seller
            # row lock held; AI quotas are reserved separately and atomically.
            try:
                answer = smart_store_reply(
                    request.user.pk, account, question.strip(),
                    use_mcp=(request.POST.get("mcp_consent") == "yes"),
                )
            except AIGatewayUnavailable:
                mode = "basic"
                answer = (
                    "Расширенный AI-ответ сейчас недоступен или достигнут лимит. "
                    "Базовые сведения из NoSale: "
                    + basic_assistant_reply(request.user.pk, account, question.strip())
                )
            with transaction.atomic():
                User.objects.select_for_update().get(pk=request.user.pk)
                # A paid Smart entitlement may expire or be revoked while the
                # model/tool call is in flight. Do not persist any stale reply.
                if not has_plan_capability(request.user.pk, "web_assistant_basic"):
                    return HttpResponse("Доступ к помощнику завершился.", status=403)
                # Recheck under the row lock: concurrent Standard or Smart
                # requests must not bypass the limit during a gateway call.
                if _assistant_message_limit_reached(request.user.pk, timezone.now(), daily_limit=daily_limit):
                    return HttpResponse("Слишком много вопросов. Повторите немного позже.", status=429)
                AssistantMessage.objects.create(
                    user_id=request.user.pk, account=account, question=question.strip(),
                    answer=answer[:2000], mode=mode,
                )
        else:
            # Serialize seller requests across Gunicorn workers: no store-switch
            # or concurrent basic question may bypass the limit.
            with transaction.atomic():
                User.objects.select_for_update().get(pk=request.user.pk)
                now = timezone.now()
                if _assistant_message_limit_reached(request.user.pk, now, daily_limit=daily_limit):
                    return HttpResponse("Слишком много вопросов. Повторите немного позже.", status=429)
                answer = basic_assistant_reply(request.user.pk, account, question.strip(), now=now)
                AssistantMessage.objects.create(
                    user_id=request.user.pk, account=account, question=question.strip(),
                    answer=answer, mode="basic", created_at=now,
                )
        return redirect("assistant_chat")

    history = list(AssistantMessage.objects.filter(
        user_id=request.user.pk, account=account
    ).order_by("-created_at", "-pk")[:20])
    history.reverse()
    return render(request, "guard/assistant.html", {
        "account": account, "history": history,
        "is_smart": has_smart_access(request.user.pk),
    })


@login_required
def store_advisor(request):
    """Smart-only, read-only recommendations from the selected user's WB store."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if not has_smart_access(request.user.pk):
        return render(request, "guard/assistant_access.html", {
            "access_title": "Рекомендации доступны в Smart",
            "access_explanation": "Для просмотра рекомендаций по вашему магазину необходим действующий тариф Smart. Базовый помощник остаётся доступен в Standard."
        }, status=403)
    account = _account(request)
    return render(request, "guard/advisor.html", {
        "account": account,
        "advice": store_recommendations(account),
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
                    if not access_snapshot(request.user.pk)["trial_started"]:
                        messages.info(request, "5 бесплатных дней начнутся после успешной первой загрузки товаров.")
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
        # Save a local product policy. Never enqueue, submit or unlock WB writes.
        if settings.SAAS_PIN_AUTH_ENABLED and not guard_is_permitted(account):
            messages.error(request, "Для изменения правил требуется действующий доступ.")
            return _dashboard_redirect(request)
        if request.POST.get("no_discount") == "on":
            allowed = 0
        else:
            form = ProductRuleForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Скидка должна быть от 0 до 99%.")
                return _dashboard_redirect(request)
            allowed = form.cleaned_data["allowed_discount"]
        with transaction.atomic():
            Product.objects.filter(pk=product.pk, account_id=account.pk).update(
                allowed_discount_override=allowed,
            )
            GuardAccount.objects.filter(pk=account.pk, user_id=request.user.pk).update(
                rules_version=F("rules_version") + 1,
            )
        GuardEvent.objects.create(
            account=account, kind="rule", nm_id=nm_id,
            message="Локальный предел скидки сохранён. Запросов на изменение WB нет.",
        )
        messages.success(request, "Правило сохранено в NoSale. Wildberries не изменялся.")
        return _dashboard_redirect(request)
    if not _account_can_write(account):
        messages.error(request, "Для изменения скидки необходим токен с правами «Чтение и запись».")
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
        elif result == "queued_basic":
            messages.info(request, f"{message} Изменение поставлено в очередь с учётом лимитов базового токена WB.")
        elif result == "read_only_token":
            messages.error(request, f"{message} Для изменения скидки нужен токен с правами «Чтение и запись».")
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
    digest_messages = PushMessage.objects.filter(user=request.user, kind__in=("digest", "smart")).order_by("-created_at")[:30]
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
        user=request.user, kind__in=("digest", "smart"), read_at__isnull=True,
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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Sum
from django.http import FileResponse, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.conf import settings

from .crypto import decrypt_token, encrypt_token
from .forms import AccountSettingsForm, DefaultDiscountForm, ProductRuleForm, RegistrationForm, TokenForm
from .models import GuardAccount, GuardEvent, Product, Upload
from .wb import WBClient, WBApiError, get_token_permissions


def health(request):
    return HttpResponse("ok", content_type="text/plain")


def favicon(request):
    response = FileResponse((settings.BASE_DIR / "portal" / "static" / "favicon.svg").open("rb"), content_type="image/svg+xml")
    response["Cache-Control"] = "public, max-age=86400"
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


def _account(user):
    account, _ = GuardAccount.objects.get_or_create(user=user)
    return account


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
def dashboard(request):
    account = _account(request.user)
    if request.method == "POST":
        action = request.POST.get("action", "apply_all")
        if action == "toggle_autocontrol":
            if not account.token_ciphertext:
                messages.error(request, "Сначала подключите токен Wildberries.")
            else:
                enabled = request.POST.get("enabled") == "on"
                GuardAccount.objects.filter(pk=account.pk).update(enabled=enabled, next_check_at=timezone.now())
                messages.success(request, "Автоисправление включено." if enabled else "Автоисправление выключено.")
            return _dashboard_redirect(request)
        if action == "apply_all":
            form = DefaultDiscountForm(request.POST)
            if form.is_valid():
                discount = form.cleaned_data["default_allowed_discount"]
                if not account.token_ciphertext:
                    messages.error(request, "Сначала подключите токен Wildberries.")
                    return redirect("settings")
                with transaction.atomic():
                    Product.objects.filter(account=account, allowed_discount_override__isnull=False).update(allowed_discount_override=None)
                    GuardAccount.objects.filter(pk=account.pk).update(
                        default_allowed_discount=discount, apply_all_requested=True,
                        next_check_at=timezone.now(), rules_version=F("rules_version") + 1,
                    )
                messages.success(request, f"Задача создана: установить скидку {discount}% для всех товаров в Wildberries. Обновите страницу через минуту.")
            else:
                messages.error(request, "Введите скидку от 0 до 99%.")
            return _dashboard_redirect(request)
        return HttpResponseNotAllowed(["GET", "POST"])
    active_products = list(Product.objects.filter(account=account, active=True).select_related("account"))
    corrected_ids = _corrected_nm_ids(account)
    filter_name = _dashboard_filter_name(request)
    filtered_products = _filter_active_products(active_products, filter_name, corrected_ids)
    products = filtered_products[:500]
    corrected_count = Upload.objects.filter(account=account, status="success").aggregate(total=Sum("corrected_count"))["total"] or 0
    over_limit_count = sum(1 for product in active_products if product.current_discount > product.allowed_discount)
    filter_labels = {
        "all": "Все товары под контролем",
        "over_limit": "Выше установленного предела",
        "corrected": "Отменённые скидки (подтверждено Wildberries)",
    }
    return render(request, "guard/dashboard.html", {
        "account": account, "products": products, "count": len(active_products),
        "shown_count": len(filtered_products), "filter_name": filter_name, "filter_label": filter_labels[filter_name],
        "corrected_count": corrected_count, "corrected_card_count": len(corrected_ids), "over_limit_count": over_limit_count,
        "product_cards_missing": bool(active_products) and not any(product.title or product.photo_url for product in active_products),
        "refresh_error": "Wildberries временно ограничил запросы. Повторная попытка будет выполнена позже." if account.last_error else "",
    })


@login_required
def settings_view(request):
    account = _account(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "token":
            token_form = TokenForm(request.POST)
            if token_form.is_valid():
                token = token_form.cleaned_data["token"].strip()
                try:
                    WBClient(token).validate_token()
                    ciphertext = encrypt_token(token)
                    with transaction.atomic():
                        pending_count = Upload.objects.filter(account=account, status="pending").update(
                            status="unknown", details="Токен заменён до подтверждения результата WB."
                        )
                        if pending_count:
                            GuardEvent.objects.create(
                                account=account, kind="upload_unknown",
                                message=f"Не удалось подтвердить {pending_count} загрузок WB: токен заменён.",
                            )
                        account.token_ciphertext = ciphertext
                        account.token_hint = token[:4] + "…" + token[-4:] if len(token) >= 12 else "сохранён"
                        account.enabled = False
                        account.next_check_at = timezone.now()
                        account.save(update_fields=["token_ciphertext", "token_hint", "enabled", "next_check_at"])
                    messages.success(request, "Доступ к товарам WB проверен, токен сохранён. Для автоисправления токену также нужны права записи.")
                    return redirect("settings")
                except (WBApiError, ValueError) as error:
                    token_form.add_error("token", f"Проверка не прошла: {error}")
            settings_form = AccountSettingsForm(instance=account)
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
            account.check_requested = True
            account.next_check_at = timezone.now()
            account.save(update_fields=["check_requested", "next_check_at"])
            messages.success(request, "Проверка поставлена в очередь. Обновите страницу через минуту.")
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
    account = _account(request.user)
    product = get_object_or_404(Product, account=account, nm_id=nm_id, active=True)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
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
    messages.success(request, f"{message} Изменение отправлено в очередь Wildberries.")
    return _dashboard_redirect(request)


@login_required
def product_history_data(request, nm_id):
    account = _account(request.user)
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


@login_required
def history(request):
    account = _account(request.user)
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
    return render(request, "guard/history.html", {"events": events[:200], "product": product})

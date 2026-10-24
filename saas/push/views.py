import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .models import NotificationPreference, PushSubscription
from .service import SUPPORTED_TIMEZONES, register_subscription, unsubscribe


def parse_json(request):
    if len(request.body) > 12000:
        raise ValueError("Превышен размер данных подписки.")
    try:
        result = json.loads(request.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Некорректный JSON.")
    if not isinstance(result, dict):
        raise ValueError("Ожидался объект.")
    return result


@login_required
@require_POST
def subscribe(request):
    if not settings.PUSH_VAPID_PUBLIC_KEY:
        return JsonResponse({"error": "Push пока не настроен."}, status=503)
    try:
        data = parse_json(request)
        subscription = register_subscription(request.user, data)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    request.session["mpno_push_endpoint"] = subscription.endpoint
    return JsonResponse({"ok": True})


@login_required
@require_POST
def unsubscribe_device(request):
    try:
        data = parse_json(request)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return JsonResponse({"error": "Некорректный адрес."}, status=400)
    unsubscribe(request.user, endpoint)
    if request.session.get("mpno_push_endpoint") == endpoint:
        request.session.pop("mpno_push_endpoint", None)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def preferences(request):
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    timezone_name = request.POST.get("timezone_name", pref.timezone_name)
    if timezone_name not in SUPPORTED_TIMEZONES:
        return JsonResponse({"error": "Выберите поддерживаемый часовой пояс."}, status=400)
    pref.timezone_name = timezone_name
    pref.instant_enabled = request.POST.get("instant_enabled") == "on"
    pref.daily_enabled = request.POST.get("daily_enabled") == "on"
    pref.save(update_fields=["instant_enabled", "daily_enabled", "timezone_name"])
    return redirect("notifications")


def deactivate_on_logout(sender, request, user, **kwargs):
    if user is not None and request is not None:
        endpoint = request.session.get("mpno_push_endpoint")
        if endpoint:
            unsubscribe(user, endpoint)

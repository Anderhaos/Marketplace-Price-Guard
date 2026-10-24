"""NoSale product connection to the existing AI Platform, mirroring MiniBook.

Owner credentials are Fernet encrypted; only the shared Platform is allowed as
an outbound origin. Provider/DeepSeek keys never enter NoSale.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from django.utils import timezone

from guard.crypto import decrypt_token, encrypt_token
from .models import OwnerAIGatewayConnection

DEFAULT_BASE_URL = "https://platform.svdev.pro/v1"
PLATFORM_HOST = "platform.svdev.pro"


class PlatformConnectionError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def normalize_base_url(value):
    parsed = urlparse((value or "").strip().rstrip("/"))
    if (parsed.scheme != "https" or parsed.hostname != PLATFORM_HOST
            or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment or parsed.path != "/v1"):
        raise PlatformConnectionError("Допускается только https://platform.svdev.pro/v1")
    return DEFAULT_BASE_URL


def project_headers():
    # The same project identity convention as MiniBook's
    # aiPlatformProjectHeaders('cdlit'), but isolated for NoSale.
    return {
        "X-AI-Project-Key": "nosale",
        "X-AI-Project-Name": "NoSale",
        "X-AI-Client-Version": "nosale-django/1",
        "X-AI-Capabilities": "chat",
    }


def save_gateway_token(connection, raw_token):
    token = (raw_token or "").strip()
    if not 16 <= len(token) <= 2048 or any(ch.isspace() for ch in token):
        raise PlatformConnectionError("Некорректный сервисный ключ AI Platform")
    connection.encrypted_service_token = encrypt_token(token)
    connection.catalog_models = []
    connection.last_checked_at = None
    connection.last_check_ok = None
    connection.last_check_message = ""


def runtime_gateway():
    """Runtime preference: deployment emergency override, then encrypted owner UI."""
    env_url = getattr(settings, "NOSALE_AI_GATEWAY_URL", "")
    env_token = getattr(settings, "NOSALE_AI_SERVICE_TOKEN", "")
    if env_url or env_token:
        if not (env_url and env_token):
            return None
        try:
            return normalize_base_url(env_url.removesuffix("/chat/completions")), env_token
        except (PlatformConnectionError, ValueError):
            return None
    conn = OwnerAIGatewayConnection.objects.filter(pk=1).first()
    if not conn or not conn.encrypted_service_token:
        return None
    try:
        return normalize_base_url(conn.base_url), decrypt_token(conn.encrypted_service_token)
    except (ValueError, PlatformConnectionError):
        return None


def fetch_platform_models(connection):
    """Explicit, owner-triggered verification of available chat models."""
    if not connection.encrypted_service_token:
        raise PlatformConnectionError("Сначала сохраните сервисный ключ")
    base = normalize_base_url(connection.base_url)
    token = decrypt_token(connection.encrypted_service_token)
    suffix = "/models?category=chat" if connection.catalog_mode == "scoped" else (
        "/models?view=full&category=chat" if connection.catalog_mode == "full" else "/models"
    )
    req = Request(base + suffix, method="GET", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        **project_headers(),
    })
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=10) as response:
            if response.status != 200:
                raise PlatformConnectionError("AI Platform вернула ошибку")
            data = response.read(524289)
    except HTTPError as exc:
        # Do not reflect upstream bodies: they may contain request or auth details.
        raise PlatformConnectionError(f"AI Platform HTTP {exc.code}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise PlatformConnectionError("AI Platform недоступна") from exc
    if len(data) > 524288:
        raise PlatformConnectionError("Каталог моделей слишком большой")
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PlatformConnectionError("Некорректный ответ каталога моделей") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PlatformConnectionError("Некорректная структура каталога AI Platform")
    models = []
    for item in payload["data"][:500]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not (1 <= len(identifier) <= 120):
            continue
        if item.get("selectable") is False:
            continue
        execution = item.get("execution")
        if isinstance(execution, dict):
            if execution.get("supported") is False:
                continue
            endpoints = execution.get("endpoints")
            if isinstance(endpoints, list) and "/v1/chat/completions" not in endpoints:
                continue
        if item.get("category") not in (None, "chat"):
            categories = item.get("categories")
            if not isinstance(categories, list) or "chat" not in categories:
                continue
        if identifier not in models:
            models.append(identifier)
    if not models:
        raise PlatformConnectionError("Нет доступных моделей чата для проекта NoSale")
    return models


def verify_gateway(connection):
    try:
        models = fetch_platform_models(connection)
    except (PlatformConnectionError, ValueError) as exc:
        connection.last_check_ok = False
        connection.last_check_message = str(exc)[:220]
        connection.last_checked_at = timezone.now()
        connection.catalog_models = []
        connection.save(update_fields=["last_check_ok", "last_check_message", "last_checked_at", "catalog_models", "updated_at"])
        raise PlatformConnectionError(str(exc)) from exc
    connection.catalog_models = models
    connection.last_checked_at = timezone.now()
    connection.last_check_ok = True
    connection.last_check_message = f"AI Platform подключена. Моделей чата: {len(models)}"
    connection.save(update_fields=["catalog_models", "last_checked_at", "last_check_ok", "last_check_message", "updated_at"])
    return models

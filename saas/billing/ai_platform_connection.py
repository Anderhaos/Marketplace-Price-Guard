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
    connection.catalog_tool_models = []
    connection.last_checked_at = None
    connection.last_check_ok = None
    connection.last_check_message = ""


def runtime_gateway(*, model_id=None):
    """Return gateway only when enabled model belongs to the verified catalog.

    Deployment-only environment credentials are explicitly managed outside the
    owner UI and remain the emergency override. Owner-managed connections fail
    closed if their stored model is withdrawn, unverified, or unavailable.
    """
    env_url = getattr(settings, "NOSALE_AI_GATEWAY_URL", "")
    env_token = getattr(settings, "NOSALE_AI_SERVICE_TOKEN", "")
    if env_url or env_token:
        if not (env_url and env_token):
            return None
        try:
            return normalize_base_url(env_url.removesuffix("/chat/completions")), env_token
        except (PlatformConnectionError, ValueError):
            return None
    # The owner must explicitly verify credentials and authorized chat models.
    # Saving an unverified key does not make Smart send commercial seller data.
    conn = OwnerAIGatewayConnection.objects.filter(pk=1, last_check_ok=True).first()
    if not conn or not conn.encrypted_service_token or not conn.catalog_models:
        return None
    if model_id is not None and (not isinstance(model_id, str) or model_id not in conn.catalog_models):
        return None
    try:
        return normalize_base_url(conn.base_url), decrypt_token(conn.encrypted_service_token)
    except (ValueError, PlatformConnectionError):
        return None


def _fetch_model_catalog(connection):
    """Fetch authorized chat models and their explicitly advertised tool support."""
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
    tool_models = []
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
            capabilities = item.get("capabilities")
            if isinstance(capabilities, dict) and capabilities.get("tools") is True:
                tool_models.append(identifier)
    if not models:
        raise PlatformConnectionError("Нет доступных моделей чата для проекта NoSale")
    return models, tool_models


def fetch_platform_models(connection):
    """Backwards-compatible chat model list; no mutation or external inference."""
    return _fetch_model_catalog(connection)[0]


def is_verified_tool_model(model_id):
    """Allow Smart MCP only for models explicitly listed with capabilities.tools=true.

    An explicit deployment emergency override retains the existing trusted
    ops contract; ordinary owner-managed catalog entries fail closed.
    """
    if not isinstance(model_id, str) or not model_id:
        return False
    if getattr(settings, "NOSALE_AI_GATEWAY_URL", "") and getattr(settings, "NOSALE_AI_SERVICE_TOKEN", ""):
        return runtime_gateway(model_id=model_id) is not None
    conn = OwnerAIGatewayConnection.objects.filter(pk=1, last_check_ok=True).first()
    return bool(
        conn and isinstance(conn.catalog_models, list)
        and isinstance(conn.catalog_tool_models, list)
        and model_id in conn.catalog_models
        and model_id in conn.catalog_tool_models
    )


def verify_gateway(connection):
    try:
        models, tool_models = _fetch_model_catalog(connection)
    except (PlatformConnectionError, ValueError) as exc:
        connection.last_check_ok = False
        connection.last_check_message = str(exc)[:220]
        connection.last_checked_at = timezone.now()
        connection.catalog_models = []
        connection.catalog_tool_models = []
        connection.save(update_fields=["last_check_ok", "last_check_message", "last_checked_at", "catalog_models", "catalog_tool_models", "updated_at"])
        raise PlatformConnectionError(str(exc)) from exc
    connection.catalog_models = models
    connection.catalog_tool_models = tool_models
    connection.last_checked_at = timezone.now()
    connection.last_check_ok = True
    connection.last_check_message = f"AI Platform подключена. Чат: {len(models)}, MCP tools: {len(tool_models)}"
    connection.save(update_fields=["catalog_models", "catalog_tool_models", "last_checked_at", "last_check_ok", "last_check_message", "updated_at"])
    return models

"""Short-lived, resource-bound signed identity delegated by NoSale's backend.

Never use user/store IDs supplied as MCP tool arguments or HTTP headers.
A live Django user, owned store and active subscription are checked again
on each MCP tool invocation (not only when the token is minted).
"""
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from billing.access import has_active_access
from guard.models import GuardAccount

RESOURCE = "http://nosale-mcp:8099/mcp"
SALT = "nosale.mcp.auth.v1"
TTL_SECONDS = 90


class AccessDenied(PermissionError):
    pass


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AccessDenied("Invalid MCP principal")
    return value


def owned_store(user_id, store_id):
    """Authoritative, fresh seller/store entitlement check every time."""
    user_id = _positive_int(user_id)
    store_id = _positive_int(store_id)
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None or not has_active_access(user_id):
        raise AccessDenied("Seller access not active")
    account = GuardAccount.objects.filter(pk=store_id, user_id=user_id).first()
    if account is None:
        raise AccessDenied("Store is not owned by seller")
    return account


def issue_store_token(user, account):
    """Call ONLY after authenticated NoSale session and server-selected store."""
    if not user or not user.is_authenticated or not user.is_active:
        raise AccessDenied("Authentication required")
    owned_store(user.pk, account.pk)
    return signing.dumps(
        {"v": 1, "u": user.pk, "s": account.pk, "aud": RESOURCE},
        salt=SALT, compress=False,
    )


def verify_store_token(raw):
    if not isinstance(raw, str) or len(raw) > 2048:
        return None
    try:
        data = signing.loads(raw, salt=SALT, max_age=TTL_SECONDS)
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("aud") != RESOURCE:
            return None
        return {"user_id": _positive_int(data.get("u")), "store_id": _positive_int(data.get("s"))}
    except (BadSignature, SignatureExpired, AccessDenied, ValueError, TypeError):
        return None


def scope_account(claims):
    if not isinstance(claims, dict):
        raise AccessDenied("MCP authorization missing")
    return owned_store(claims.get("user_id"), claims.get("store_id"))

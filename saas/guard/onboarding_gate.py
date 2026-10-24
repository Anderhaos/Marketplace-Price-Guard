"""Require WB onboarding before first use of seller features.

Middleware restricts only SaaS PIN-auth flow. Legacy mode and login/recovery
routes remain intact. Owner checks remain inside each view.
"""
from django.conf import settings
from django.shortcuts import redirect

from .models import GuardAccount


class RequireWbConnectionMiddleware:
    PRIVATE_VIEWS = frozenset({
        "stores", "select_store", "settings", "product_rule",
        "product_history_data", "history", "notifications",
        "mark_notifications_read", "push_subscribe", "push_unsubscribe",
        "push_preferences",
    })

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not settings.SAAS_PIN_AUTH_ENABLED or not request.user.is_authenticated:
            return None
        match = request.resolver_match
        if not match or match.url_name not in self.PRIVATE_VIEWS:
            return None
        if not GuardAccount.objects.filter(
            user_id=request.user.pk,
            token_ciphertext__gt="",
        ).exists():
            return redirect("dashboard")
        return None

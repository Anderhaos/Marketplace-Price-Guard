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
        "push_preferences", "subscription", "billing_checkout", "store_advisor",
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
        from .stores import SESSION_KEY
        stores = GuardAccount.objects.filter(user_id=request.user.pk).order_by("id")
        store_id = request.session.get(SESSION_KEY)
        selected = None
        if store_id is not None:
            try:
                selected = stores.filter(pk=int(store_id)).first()
            except (ValueError, TypeError):
                pass
        if selected is None:
            selected = stores.first()
        if (selected is None or not selected.token_ciphertext
                or selected.initial_sync_completed_at is None):
            return redirect("dashboard")
        return None

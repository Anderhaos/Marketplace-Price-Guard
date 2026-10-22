"""Per-request seller-store selection. All access MUST be owner-scoped."""
from .models import GuardAccount

SESSION_KEY = "mpno_selected_store_id"


def selected_store(request):
    shops = GuardAccount.objects.filter(user=request.user).order_by("id")
    selected_id = request.session.get(SESSION_KEY)
    if selected_id is not None:
        try:
            selected = shops.filter(pk=int(selected_id)).first()
        except (ValueError, TypeError):
            selected = None
        if selected:
            return selected
        request.session.pop(SESSION_KEY, None)
    selected = shops.first()
    if selected is None:
        selected = GuardAccount.objects.create(user=request.user)
    request.session[SESSION_KEY] = selected.pk
    return selected

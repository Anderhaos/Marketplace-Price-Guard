"""Owner-only SaaS console. No client-store actions or arbitrary providers.

All routes are wrapped with admin.site.admin_view in portal.urls, preserving
PIN authentication, owner email step-up, session enforcement and CSRF.
"""
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.forms import modelform_factory
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .admin import OwnerAIGatewayConnectionForm, OwnerAISettingsForm
from .ai_platform_connection import PlatformConnectionError, verify_gateway
from .models import (
    OwnerAISettings, OwnerAIGatewayConnection, OwnerMCPAdapter,
    OwnerMCPSettings, OwnerOperationsSettings, OwnerTariffOffer,
)
from .owner_readiness import integration_readiness


NAV = (
    ("overview", "Обзор", "/admin/"),
    ("ai", "AI и модели", "/admin/control/ai/"),
    ("mcp", "MCP и площадки", "/admin/control/mcp/"),
    ("operations", "Автоматизация", "/admin/control/operations/"),
    ("tariffs", "Тарифы", "/admin/control/tariffs/"),
)

MCP_FIELDS = (
    "enabled", "wb_enabled", "wb_overview_enabled", "wb_products_enabled",
    "wb_events_enabled", "wb_promotions_enabled", "ozon_status_enabled",
    "max_products", "max_events", "max_promotions",
)
OPERATIONS_FIELDS = (
    "guard_checks_enabled", "instant_events_enabled", "daily_digests_enabled",
    "push_delivery_enabled", "guard_poll_seconds", "notifications_poll_seconds",
    "push_batch_size",
)
MCPForm = modelform_factory(OwnerMCPSettings, fields=MCP_FIELDS)
OperationsForm = modelform_factory(OwnerOperationsSettings, fields=OPERATIONS_FIELDS)
AdapterForm = modelform_factory(OwnerMCPAdapter, fields=("enabled",))


class TariffForm(forms.ModelForm):
    price_rub = forms.DecimalField(
        label="Цена за месяц, ₽", max_digits=8, decimal_places=2,
        min_value=Decimal("1.00"), max_value=Decimal("100000.00"),
    )

    class Meta:
        model = OwnerTariffOffer
        fields = ("name", "enabled")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.instance._state.adding:
            self.fields["price_rub"].initial = Decimal(self.instance.price_kopeks) / 100

    def save(self, commit=True):
        offer = super().save(commit=False)
        offer.price_kopeks = int(self.cleaned_data["price_rub"] * 100)
        if commit:
            offer.save()
        return offer


def _audit(request, instance, description, *, created=False):
    """Log operation name, never submitted values, tokens or prompts."""
    LogEntry.objects.create(
        user=request.user,
        content_type=ContentType.objects.get_for_model(instance),
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
        action_flag=ADDITION if created else CHANGE,
        change_message=description,
    )


def _context(request, *, active, **extra):
    return {
        "current": active, "nav": NAV, "readiness": integration_readiness(),
        **extra,
    }


@require_http_methods(["GET"])
def owner_home(request):
    """Real DB state, not synthetic uptime or provider health."""
    return render(request, "admin/owner_home.html", _context(request, active="overview"))


@require_http_methods(["GET", "POST"])
def owner_section(request, section):
    """Bounded owner settings forms with explicit POST actions and audit."""
    titles = {
        "ai": "AI Platform и модели",
        "mcp": "MCP и площадки",
        "operations": "Автоматизация и уведомления",
        "tariffs": "Тарифы и продажи",
    }
    if section not in titles:
        from django.http import Http404
        raise Http404("Unknown console section")

    ai = OwnerAISettings.objects.filter(pk=1).first() or OwnerAISettings(pk=1)
    gateway = OwnerAIGatewayConnection.objects.filter(pk=1).first() or OwnerAIGatewayConnection(pk=1)
    mcp = OwnerMCPSettings.runtime()
    operations = OwnerOperationsSettings.runtime()
    adapters = {a.slug: a for a in OwnerMCPAdapter.objects.filter(slug__in=("wb", "ozon"))}
    offers = {o.slug: o for o in OwnerTariffOffer.objects.filter(slug__in=("standard", "smart"))}
    for slug in ("standard", "smart"):
        offers.setdefault(slug, OwnerTariffOffer(slug=slug, name=slug.title(), price_kopeks=0))

    forms_by_action = {}
    if section == "ai":
        if gateway is not None:
            forms_by_action["gateway"] = (OwnerAIGatewayConnectionForm, gateway)
        if ai is not None:
            forms_by_action["ai"] = (OwnerAISettingsForm, ai)
    elif section == "mcp":
        if mcp is not None:
            forms_by_action["mcp"] = (MCPForm, mcp)
        for slug in ("wb", "ozon"):
            if slug in adapters:
                forms_by_action["adapter_" + slug] = (AdapterForm, adapters[slug])
    elif section == "operations":
        if operations is not None:
            forms_by_action["operations"] = (OperationsForm, operations)
    elif section == "tariffs":
        for slug in ("standard", "smart"):
            if slug in offers:
                forms_by_action["tariff_" + slug] = (TariffForm, offers[slug])

    action = request.POST.get("_panel", "") if request.method == "POST" else ""
    bound = {}
    if request.method == "POST":
        if action == "verify-gateway" and section == "ai" and gateway is not None:
            if not gateway.configured:
                messages.error(request, "Сначала сохраните сервисный ключ AI Platform.")
                return redirect(reverse("nosale_owner_section", args=[section]))
            try:
                found = verify_gateway(gateway)  # Explicit owner POST; network request.
            except (ValueError, PlatformConnectionError):
                messages.error(request, "Подключение не подтверждено. Проверьте ключ и настройки.")
            else:
                _audit(request, gateway, "Проверка подключения AI Platform")
                messages.success(request, f"Каталог подтверждён. Моделей чата: {len(found)}.")
            return redirect(reverse("nosale_owner_section", args=[section]))
        if action not in forms_by_action:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest("Неизвестная операция с настройками.")
        form_cls, instance = forms_by_action[action]
        bound[action] = form_cls(request.POST, instance=instance, prefix=action)
        if bound[action].is_valid():
            with transaction.atomic():
                is_new = instance._state.adding
                instance = bound[action].save()
                _audit(request, instance, "Настройки изменены в панели владельца NoSale", created=is_new)
            messages.success(request, "Настройки сохранены.")
            return redirect(reverse("nosale_owner_section", args=[section]))

    rendered_forms = {
        name: bound.get(name) or cls(instance=instance, prefix=name)
        for name, (cls, instance) in forms_by_action.items()
    }
    return render(request, "admin/owner_section.html", _context(
        request, active=section, section=section, title=titles[section],
        forms=rendered_forms, gateway=gateway, adapters=adapters, offers=offers,
        form_errors=bool(bound), advanced_links=True,
    ))


def owner_integrations(request):
    """Preserved compatibility endpoint; read-only historical diagnostics."""
    context = {
        **admin.site.each_context(request),
        "title": "Интеграции и готовность NoSale",
        "readiness": integration_readiness(),
        "has_permission": True,
    }
    return render(request, "admin/owner_integrations.html", context)

"""Owner-only integration readiness view and audit navigation.

This view is always wrapped in Django admin's authenticated permission gate.
It never calls external providers, decrypts integration keys or mutates config.
"""
from django.contrib import admin
from django.shortcuts import render

from .owner_readiness import integration_readiness


def owner_integrations(request):
    context = {
        **admin.site.each_context(request),
        "title": "Интеграции и готовность NoSale",
        "readiness": integration_readiness(),
        "has_permission": True,
    }
    return render(request, "admin/owner_integrations.html", context)

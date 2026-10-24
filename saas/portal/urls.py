from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.urls import path
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.views.generic import RedirectView

from guard import views, email_pin
from guard.forms import EmailLoginForm
from push import views as push_views
from billing import views as billing_views

def admin_login_gateway(request):
    """Use the rate-limited email/PIN login instead of Django's raw password form.

    A four-digit seller PIN must never be authenticated through an unthrottled
    second form. Legacy deployments retain the standard admin login.
    """
    if not settings.SAAS_PIN_AUTH_ENABLED:
        return admin.site.login(request)
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.user.is_authenticated:
        return redirect("admin:index" if request.user.is_staff else "dashboard")
    return redirect("/login/?next=/admin/")


urlpatterns = [
    path("admin/login/", admin_login_gateway, name="nosale_admin_pin_login"),
    path("favicon.ico", RedirectView.as_view(url="/favicon.svg", permanent=False)),
    path("favicon.svg", views.favicon, name="favicon"),
    path("manifest.webmanifest", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.pwa_service_worker, name="pwa_service_worker"),
    path("admin/", admin.site.urls),
    path("", views.dashboard, name="dashboard"),
    path("connect/wb/", views.connect_wb, name="connect_wb"),
    path("connect/wb/setup/", views.onboarding_connect, name="onboarding_connect"),
    path("billing/", billing_views.subscription, name="subscription"),
    path("billing/checkout/", billing_views.checkout, name="billing_checkout"),
    path("billing/return/", billing_views.payment_return, name="billing_return"),
    path("billing/tochka/webhook/", billing_views.tochka_webhook, name="tochka_webhook"),

    path("register/", email_pin.registration_entry, name="register"),
    path("auth/code/", email_pin.verify_code, name="auth_code"),
    path("auth/pin/", email_pin.finish_pin, name="auth_pin"),
    path("verify/<uidb64>/<token>/", email_pin.legacy_verify_entry, name="verify"),
    path("login/", email_pin.login_entry, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", email_pin.recovery_entry, name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(template_name="guard/message.html", extra_context={"notice": "Письмо для восстановления пароля отправлено."}), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="guard/password_reset_confirm.html", success_url="/login/"), name="password_reset_confirm"),
    path("stores/", views.stores, name="stores"),
    path("stores/select/<int:store_id>/", views.select_store, name="select_store"),
    path("assistant/", views.assistant_chat, name="assistant_chat"),
    path("advisor/", views.store_advisor, name="store_advisor"),
    path("settings/", views.settings_view, name="settings"),
    path("products/<int:nm_id>/", views.product_rule, name="product_rule"),
    path("products/<int:nm_id>/history-data/", views.product_history_data, name="product_history_data"),
    path("history/", views.history, name="history"),
    path("notifications/", views.notifications, name="notifications"),
    path("push/subscribe/", push_views.subscribe, name="push_subscribe"),
    path("push/unsubscribe/", push_views.unsubscribe_device, name="push_unsubscribe"),
    path("push/preferences/", push_views.preferences, name="push_preferences"),
    path("notifications/read/", views.mark_notifications_read, name="mark_notifications_read"),
    path("health/", views.health, name="health"),
]

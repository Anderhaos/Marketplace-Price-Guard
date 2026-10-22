from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.urls import path
from django.views.generic import RedirectView

from guard import views, email_pin
from guard.forms import EmailLoginForm
from push import views as push_views

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/favicon.svg", permanent=False)),
    path("favicon.svg", views.favicon, name="favicon"),
    path("manifest.webmanifest", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.pwa_service_worker, name="pwa_service_worker"),
    path("admin/", admin.site.urls),
    path("", views.dashboard, name="dashboard"),
    path("register/", email_pin.start_registration if settings.SAAS_PIN_AUTH_ENABLED else views.register, name="register"),
    path("auth/code/", email_pin.verify_code, name="auth_code"),
    path("auth/pin/", email_pin.finish_pin, name="auth_pin"),
    path("verify/<uidb64>/<token>/", email_pin.legacy_link_disabled if settings.SAAS_PIN_AUTH_ENABLED else views.verify, name="verify"),
    path("login/", email_pin.pin_login if settings.SAAS_PIN_AUTH_ENABLED else auth_views.LoginView.as_view(template_name="guard/login.html", authentication_form=EmailLoginForm, extra_context={"password_reset_available": settings.REQUIRE_EMAIL_VERIFICATION}), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", email_pin.start_recovery if settings.SAAS_PIN_AUTH_ENABLED else (auth_views.PasswordResetView.as_view(template_name="guard/password_reset.html", email_template_name="guard/password_reset_email.txt", success_url="/password-reset/sent/") if settings.REQUIRE_EMAIL_VERIFICATION else views.password_reset_unavailable), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(template_name="guard/message.html", extra_context={"notice": "Письмо для восстановления пароля отправлено."}), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="guard/password_reset_confirm.html", success_url="/login/"), name="password_reset_confirm"),
    path("stores/", views.stores, name="stores"),
    path("stores/select/<int:store_id>/", views.select_store, name="select_store"),
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

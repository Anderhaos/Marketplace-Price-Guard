from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.urls import path
from django.views.generic import RedirectView

from guard import views
from guard.forms import EmailLoginForm

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url="/favicon.svg", permanent=False)),
    path("favicon.svg", views.favicon, name="favicon"),
    path("admin/", admin.site.urls),
    path("", views.dashboard, name="dashboard"),
    path("register/", views.register, name="register"),
    path("verify/<uidb64>/<token>/", views.verify, name="verify"),
    path("login/", auth_views.LoginView.as_view(template_name="guard/login.html", authentication_form=EmailLoginForm, extra_context={"password_reset_available": settings.REQUIRE_EMAIL_VERIFICATION}), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", auth_views.PasswordResetView.as_view(template_name="guard/password_reset.html", email_template_name="guard/password_reset_email.txt", success_url="/password-reset/sent/") if settings.REQUIRE_EMAIL_VERIFICATION else views.password_reset_unavailable, name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(template_name="guard/message.html", extra_context={"notice": "Письмо для восстановления пароля отправлено."}), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="guard/password_reset_confirm.html", success_url="/login/"), name="password_reset_confirm"),
    path("settings/", views.settings_view, name="settings"),
    path("products/<int:nm_id>/", views.product_rule, name="product_rule"),
    path("products/<int:nm_id>/history-data/", views.product_history_data, name="product_history_data"),
    path("history/", views.history, name="history"),
    path("health/", views.health, name="health"),
]

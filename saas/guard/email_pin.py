"""MP NoSale SaaS email verification code -> PIN registration and PIN recovery.

Legacy seller service is separate and never imports this module. Challenges and
login throttles live in SaaS PostgreSQL, not process-local state.
"""
import secrets
import os
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import transaction
from django.db.utils import OperationalError
import logging
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from .mail_templates import send_pin_verification_email
from .models import EmailPinChallenge, EmailPinLoginThrottle, GuardAccount

log = logging.getLogger(__name__)
SESSION_CHALLENGE = "mpno_saas_email_challenge_id"
SESSION_PREVIEW_CODE = "mpno_saas_test_preview_code"


def _preview_code_permitted(request):
    """Staging-only test tool, guarded against real production configurations."""
    return (
        settings.DEBUG
        and os.environ.get("NOSALE_LOCAL_EMAIL_PREVIEW") == "1"
        and settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
        and not settings.SAAS_WB_NETWORK_ENABLED
        and not settings.TOCHKA_BILLING_ENABLED
        and request.get_host().split(":")[0] in ("127.0.0.1", "localhost")
    )

CODE_TTL = timedelta(minutes=10)
CODE_RESEND_WAIT = timedelta(seconds=60)
VERIFIED_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 5
MAX_PIN_ATTEMPTS = 5
PIN_LOCK_TTL = timedelta(minutes=15)


class EmailForm(forms.Form):
    email = forms.EmailField(label="Email", max_length=254)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class CodeForm(forms.Form):
    code = forms.RegexField(label="Код из письма", regex=r"^\d{4}$",
                            widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": "4", "pattern": "[0-9]{4}"}),
                            error_messages={"invalid": "Введите 4 цифры из письма."})


class PinForm(forms.Form):
    pin = forms.RegexField(label="PIN (4 цифры)", regex=r"^\d{4}$",
                           widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "inputmode": "numeric", "maxlength": "4"}),
                           error_messages={"invalid": "PIN должен состоять из 4 цифр."})
    confirm_pin = forms.CharField(label="Повторите PIN", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password", "inputmode": "numeric", "maxlength": "4"}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pin") and cleaned.get("confirm_pin") != cleaned["pin"]:
            self.add_error("confirm_pin", "PIN не совпадают.")
        if cleaned.get("pin") and len(set(cleaned["pin"])) == 1:
            self.add_error("pin", "Выберите менее простой PIN.")
        return cleaned


class PinLoginForm(EmailForm):
    # Six-digit PINs created before this rollout remain usable until reset.
    pin = forms.RegexField(label="PIN (4 цифры)", regex=r"^\d{4}(\d{2})?$", widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password", "inputmode": "numeric", "maxlength": "6"}))


def _existing_user(email):
    return User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()


def _start(request, purpose):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        exists = _existing_user(email) is not None
        if purpose == EmailPinChallenge.PURPOSE_REGISTER and exists:
            form.add_error("email", "Адрес уже зарегистрирован. Используйте вход или восстановление PIN.")
        else:
            now = timezone.now()
            with transaction.atomic():
                challenge, created = EmailPinChallenge.objects.select_for_update().get_or_create(
                    email=email, purpose=purpose,
                    defaults={"sent_at": now - CODE_RESEND_WAIT, "expires_at": now},
                )
                request.session[SESSION_CHALLENGE] = challenge.pk
                if not exists and purpose == EmailPinChallenge.PURPOSE_RECOVER:
                    # No account enumeration in recovery. Keep a locked challenge with
                    # no valid code; the same UI is shown to the requester.
                    challenge.code_hash = ""
                    challenge.attempts = MAX_CODE_ATTEMPTS
                    challenge.expires_at = now + CODE_TTL
                    challenge.verified_until = None
                    challenge.save(update_fields=["code_hash", "attempts", "expires_at", "verified_until"])
                elif (not created and challenge.expires_at > now
                      and (challenge.attempts >= MAX_CODE_ATTEMPTS
                           or now - challenge.sent_at < CODE_RESEND_WAIT)):
                    # Repeated requests must never silently claim that a new code
                    # was sent when only the existing challenge is still active.
                    if challenge.attempts >= MAX_CODE_ATTEMPTS:
                        messages.info(request, "Попытки исчерпаны. Новый код можно запросить после истечения текущего.")
                    else:
                        messages.info(request, "Код уже отправлен. Повторный запрос доступен через 60 секунд.")
                else:
                    code = f"{secrets.randbelow(10000):04d}"
                    challenge.code_hash = make_password(code)
                    challenge.attempts = 0
                    challenge.sent_at = now
                    challenge.expires_at = now + CODE_TTL
                    challenge.verified_until = None
                    challenge.save(update_fields=[
                        "code_hash", "attempts", "sent_at", "expires_at", "verified_until",
                    ])
                    # Fail closed: the DB challenge is rolled back if the relay fails.
                    send_pin_verification_email(email=email, code=code, purpose=purpose)
                    if _preview_code_permitted(request):
                        # The code is available only in this temporary browser session.
                        request.session[SESSION_PREVIEW_CODE] = {
                            "challenge_id": challenge.pk, "code": code,
                        }
            return redirect("auth_code")
    return render(request, "guard/auth_email.html", {
        "form": form,
        "purpose": purpose,
    })


def start_registration(request):
    return _start(request, EmailPinChallenge.PURPOSE_REGISTER)


def start_recovery(request):
    return _start(request, EmailPinChallenge.PURPOSE_RECOVER)


def _current_challenge(request, *, require_verified=False):
    challenge_id = request.session.get(SESSION_CHALLENGE)
    if not isinstance(challenge_id, int):
        return None
    try:
        challenge = EmailPinChallenge.objects.get(pk=challenge_id)
    except EmailPinChallenge.DoesNotExist:
        return None
    now = timezone.now()
    if require_verified:
        return challenge if (challenge.verified_until and challenge.verified_until > now) else None
    return challenge if challenge.expires_at > now else None


def verify_code(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    challenge = _current_challenge(request)
    if challenge is None:
        messages.error(request, "Срок действия кода истёк. Запросите новый код.")
        return redirect("register")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            challenge = EmailPinChallenge.objects.select_for_update().get(pk=challenge.pk)
            now = timezone.now()
            if (challenge.expires_at <= now or challenge.attempts >= MAX_CODE_ATTEMPTS
                    or not challenge.code_hash):
                form.add_error("code", "Код недействителен. Запросите новый.")
            elif check_password(form.cleaned_data["code"], challenge.code_hash):
                challenge.code_hash = ""
                challenge.verified_until = now + VERIFIED_TTL
                challenge.save(update_fields=["code_hash", "verified_until"])
                request.session.pop(SESSION_PREVIEW_CODE, None)
                return redirect("auth_pin")
            else:
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                form.add_error("code", "Код неверный или истёк.")
    preview_code = None
    if _preview_code_permitted(request):
        entry = request.session.get(SESSION_PREVIEW_CODE)
        if (isinstance(entry, dict) and entry.get("challenge_id") == challenge.pk
                and challenge.code_hash and challenge.attempts < MAX_CODE_ATTEMPTS
                and challenge.expires_at > timezone.now()):
            preview_code = entry.get("code")
    return render(request, "guard/auth_verify.html", {
        "form": form, "purpose": challenge.purpose,
        "email": challenge.email, "preview_code": preview_code,
    })


def finish_pin(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    # A completed registration consumes the one-time challenge. Browser
    # resubmission, refresh and double clicks must remain idempotent.
    if request.user.is_authenticated:
        return redirect("dashboard")
    challenge = _current_challenge(request, require_verified=True)
    if challenge is None:
        messages.error(request, "Подтверждение почты завершено или истекло. Войдите или начните регистрацию заново.")
        return redirect("login")
    form = PinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                challenge = EmailPinChallenge.objects.select_for_update().get(pk=challenge.pk)
                if not challenge.verified_until or challenge.verified_until <= timezone.now():
                    form.add_error(None, "Подтверждение истекло. Начните заново.")
                elif challenge.purpose == "register":
                    if _existing_user(challenge.email):
                        # Another request might have completed this exact signup.
                        # Do not silently recreate it or reveal PIN validity.
                        form.add_error(None, "Аккаунт уже создан. Перейдите ко входу.")
                    else:
                        user = User(username=challenge.email, email=challenge.email, is_active=True)
                        user.set_password(form.cleaned_data["pin"])
                        user.save()
                        GuardAccount.objects.create(user=user)
                        challenge.delete()
                        request.session.pop(SESSION_CHALLENGE, None)
                        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                        return redirect("dashboard")
                elif challenge.purpose == "recover":
                    user = _existing_user(challenge.email)
                    if user is None:
                        form.add_error(None, "Не удалось восстановить PIN.")
                    else:
                        user.set_password(form.cleaned_data["pin"])
                        user.save(update_fields=["password"])
                        EmailPinLoginThrottle.objects.filter(email=challenge.email).delete()
                        challenge.delete()
                        request.session.pop(SESSION_CHALLENGE, None)
                        messages.success(request, "PIN обновлён. Войдите с новым PIN.")
                        return redirect("login")
        except OperationalError:
            # SQLite on Windows (local preview) can fail an interleaved
            # read-to-write transaction with "database is locked". Preserve
            # the verified challenge so the user can retry safely.
            log.exception("nosale_pin_finalization_db_failure")
            form.add_error(None, "Не удалось сохранить PIN из-за временной ошибки базы. Повторите попытку.")
    return render(request, "guard/auth_pin.html", {
        "form": form, "purpose": challenge.purpose, "email": challenge.email,
    })


SESSION_LOGIN_EMAIL = "nosale_login_email"
SESSION_LOGIN_NEXT = "nosale_login_next"


def pin_login(request):
    """Email-first gateway: known sellers enter PIN, new sellers get a code.

    Existing two-field POSTs remain valid for users already at the PIN step
    and for compatibility with password recovery and prior clients.
    """
    if request.user.is_authenticated:
        if request.GET.get("next") == "/admin/" and request.user.is_staff:
            return redirect("/admin/")
        return redirect("dashboard")
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    stage = "email"
    email = ""
    if request.method == "GET":
        request.session.pop(SESSION_LOGIN_EMAIL, None)
        # Only the exact internal admin URL may be restored post-login.
        # Never trust user-provided external or scheme-relative next URLs.
        if request.GET.get("next") == "/admin/":
            request.session[SESSION_LOGIN_NEXT] = "/admin/"
        else:
            request.session.pop(SESSION_LOGIN_NEXT, None)
        form = EmailForm()
    elif "pin" not in request.POST:
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            if _existing_user(email) is None:
                request.session.pop(SESSION_LOGIN_EMAIL, None)
                # Registration uses the same verified-email challenge as the
                # existing explicit /register/ route; no account is created yet.
                return _start(request, EmailPinChallenge.PURPOSE_REGISTER)
            request.session[SESSION_LOGIN_EMAIL] = email
            stage = "pin"
            form = PinLoginForm(initial={"email": email})
    else:
        stage = "pin"
        form = PinLoginForm(request.POST)
        email = request.POST.get("email", "").strip().lower()
        if form.is_valid():
            email = form.cleaned_data["email"]
            pin = form.cleaned_data["pin"]
            now = timezone.now()
            with transaction.atomic():
                throttle, _ = EmailPinLoginThrottle.objects.select_for_update().get_or_create(email=email)
                if throttle.locked_until and throttle.locked_until > now:
                    form.add_error(None, "Слишком много попыток входа. Попробуйте позже или восстановите PIN.")
                else:
                    user = authenticate(request, username=email, password=pin)
                    if user is None:
                        if not throttle.first_failed_at or now - throttle.first_failed_at > PIN_LOCK_TTL:
                            throttle.first_failed_at = now
                            throttle.attempts = 0
                        throttle.attempts += 1
                        if throttle.attempts >= MAX_PIN_ATTEMPTS:
                            throttle.lock_count = min(throttle.lock_count + 1, 3)
                            lock_times = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=24))
                            throttle.locked_until = now + lock_times[throttle.lock_count - 1]
                        throttle.save()
                        form.add_error(None, "Неверный email или PIN.")
                    else:
                        throttle.delete()
                        request.session.pop(SESSION_LOGIN_EMAIL, None)
                        requested = request.session.pop(SESSION_LOGIN_NEXT, "")
                        login(request, user)
                        if requested == "/admin/" and user.is_staff:
                            return redirect("/admin/")
                        return redirect("dashboard")
    return render(request, "guard/login_pin.html", {
        "form": form, "stage": stage, "email": email,
    })


def legacy_link_disabled(request, *args, **kwargs):
    return render(request, "guard/message.html", {
        "notice": "Подтверждение по ссылке заменено кодом. Зарегистрируйтесь заново.",
    }, status=410)


# Dynamic feature routing: preserves existing staging behavior while the new
# email/PIN flow is disabled, and remains toggleable in Django tests.
def registration_entry(request):
    if settings.SAAS_PIN_AUTH_ENABLED:
        return start_registration(request)
    from .views import register
    return register(request)


def login_entry(request):
    if settings.SAAS_PIN_AUTH_ENABLED:
        return pin_login(request)
    from django.contrib.auth.views import LoginView
    from .forms import EmailLoginForm
    return LoginView.as_view(
        template_name="guard/login.html",
        authentication_form=EmailLoginForm,
        extra_context={"password_reset_available": settings.REQUIRE_EMAIL_VERIFICATION},
    )(request)


def legacy_verify_entry(request, uidb64, token):
    if settings.SAAS_PIN_AUTH_ENABLED:
        return legacy_link_disabled(request)
    from .views import verify
    return verify(request, uidb64, token)


def recovery_entry(request):
    if settings.SAAS_PIN_AUTH_ENABLED:
        return start_recovery(request)
    if not settings.REQUIRE_EMAIL_VERIFICATION:
        from .views import password_reset_unavailable
        return password_reset_unavailable(request)
    from django.contrib.auth.views import PasswordResetView
    return PasswordResetView.as_view(
        template_name="guard/password_reset.html",
        email_template_name="guard/password_reset_email.txt",
        success_url="/password-reset/sent/",
    )(request)

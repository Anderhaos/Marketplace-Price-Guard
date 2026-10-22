"""MP NoSale SaaS email verification code -> PIN registration and PIN recovery.

Legacy seller service is separate and never imports this module. Challenges and
login throttles live in SaaS PostgreSQL, not process-local state.
"""
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import EmailPinChallenge, EmailPinLoginThrottle, GuardAccount

SESSION_CHALLENGE = "mpno_saas_email_challenge_id"
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
                    # Repeated requests cannot reset the attempt counter or issue
                    # a fresh code until cooldown / challenge expiry.
                    pass
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
                    subject = "Код подтверждения MP NoSale" if purpose == "register" else "Восстановление PIN MP NoSale"
                    # Fail closed: DB transaction rolls back if delivery fails.
                    send_mail(subject, f"Код: {code}\nДействует 10 минут. Если вы не запрашивали код, проигнорируйте письмо.",
                              settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
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
                return redirect("auth_pin")
            else:
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                form.add_error("code", "Код неверный или истёк.")
    return render(request, "guard/auth_verify.html", {"form": form, "purpose": challenge.purpose})


def finish_pin(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    challenge = _current_challenge(request, require_verified=True)
    if challenge is None:
        messages.error(request, "Подтвердите email повторно.")
        return redirect("register")
    form = PinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            challenge = EmailPinChallenge.objects.select_for_update().get(pk=challenge.pk)
            if not challenge.verified_until or challenge.verified_until <= timezone.now():
                form.add_error(None, "Подтверждение истекло. Начните заново.")
            elif challenge.purpose == "register":
                if _existing_user(challenge.email):
                    form.add_error(None, "Аккаунт уже существует. Выполните вход.")
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
                    challenge.delete()
                    request.session.pop(SESSION_CHALLENGE, None)
                    messages.success(request, "PIN обновлён. Войдите с новым PIN.")
                    return redirect("login")
    return render(request, "guard/auth_pin.html", {"form": form, "purpose": challenge.purpose})


def pin_login(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    form = PinLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        pin = form.cleaned_data["pin"]
        now = timezone.now()
        with transaction.atomic():
            throttle, _ = EmailPinLoginThrottle.objects.select_for_update().get_or_create(email=email)
            if throttle.locked_until and throttle.locked_until > now:
                form.add_error(None, "Слишком много попыток. Попробуйте через 15 минут.")
            else:
                user = authenticate(request, username=email, password=pin)
                if user is None:
                    if not throttle.first_failed_at or now - throttle.first_failed_at > PIN_LOCK_TTL:
                        throttle.first_failed_at = now
                        throttle.attempts = 0
                    throttle.attempts += 1
                    if throttle.attempts >= MAX_PIN_ATTEMPTS:
                        throttle.locked_until = now + PIN_LOCK_TTL
                    throttle.save()
                    form.add_error(None, "Неверный email или PIN.")
                else:
                    throttle.delete()
                    login(request, user)
                    return redirect("dashboard")
    return render(request, "guard/login_pin.html", {"form": form})


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

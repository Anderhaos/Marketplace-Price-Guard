from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import validate_email

from .models import GuardAccount


class DefaultDiscountForm(forms.Form):
    default_allowed_discount = forms.IntegerField(label="Максимальная скидка для всех товаров, %", min_value=0, max_value=99)


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(label="Email", max_length=254)

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email", max_length=254)

    class Meta:
        model = User
        fields = ("email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        validate_email(email)
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Этот email уже зарегистрирован.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = user.email
        user.is_active = False
        if commit:
            user.save()
            GuardAccount.objects.create(user=user)
        return user


class AccountSettingsForm(forms.ModelForm):
    class Meta:
        model = GuardAccount
        fields = ("interval_minutes", "max_fixes")
        labels = {
            "interval_minutes": "Интервал проверки, минут",
            "max_fixes": "Максимум товаров за проверку",
        }


class TokenForm(forms.Form):
    token = forms.CharField(
        label="Новый API-токен Wildberries",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Вставьте сюда полный токен Wildberries"}),
    )


class ProductRuleForm(forms.Form):
    allowed_discount = forms.IntegerField(label="Индивидуальный предел скидки, %", min_value=0, max_value=99, required=False)

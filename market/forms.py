from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django import forms


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Электронная почта")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Эта почта уже используется.")
        return email

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "email", "password1", "password2")


class TelegramRegistrationForm(forms.Form):
    email = forms.EmailField(label="Электронная почта")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Эта почта уже используется. Войдите в существующий аккаунт и привяжите Telegram в профиле.")
        return email

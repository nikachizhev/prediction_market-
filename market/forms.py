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


class ProfileForm(forms.Form):
    field_order = ["name", "pseudonym", "show_real_name", "birth_date", "city", "workplace", "education", "bio", "github_login"]
    birth_date = forms.DateField(label="Дата рождения", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    city = forms.CharField(label="Город", max_length=100, required=False)
    workplace = forms.CharField(label="Место работы", max_length=200, required=False)
    education = forms.CharField(label="Место учёбы", max_length=200, required=False)
    bio = forms.CharField(label="О себе", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_birth_date(self):
        from django.utils import timezone
        value = self.cleaned_data["birth_date"]
        if value and value > timezone.localdate():
            raise forms.ValidationError("Дата рождения не может быть в будущем.")
        return value

    name = forms.CharField(label="Имя", max_length=100)
    pseudonym = forms.CharField(label="Псевдоним", max_length=100)
    show_real_name = forms.BooleanField(label="Показывать имя вместо псевдонима", required=False)
    github_login = forms.RegexField(
        label="Логин GitHub", regex=r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$",
        required=False, max_length=39,
        error_messages={"invalid": "Укажите логин GitHub без @ и ссылки."},
    )

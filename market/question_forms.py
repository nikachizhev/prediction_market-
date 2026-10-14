from django import forms
from django.utils import timezone
from .models import Question, Group


class CreateQuestionForm(forms.ModelForm):
    group = forms.ModelChoiceField(label="Где опубликовать", queryset=Group.objects.none(), required=False, empty_label="Общий рынок — виден всем")
    option_lines = forms.CharField(label="Варианты (каждый с новой строки)", widget=forms.Textarea, required=False)

    def __init__(self, *args, trader=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Group
        self.fields["group"].queryset = Group.objects.filter(memberships__trader=trader, memberships__status="active") if trader else Group.objects.none()

    class Meta:
        model = Question
        fields = ("title", "group", "kind", "tracks_deadline", "resolution_criteria", "deadline", "b", "number_min", "number_max", "tolerance")
        labels = {"kind": "Тип", "b": "Начальная ликвидность", "number_min": "Минимальное число", "number_max": "Максимальное число", "tolerance": "Допустимая ошибка"}
        widgets = {"deadline": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def clean(self):
        data = super().clean()
        if data.get("tracks_deadline") and (data.get("kind") not in ("lmsr", "ls_lmsr", "cpmm") or not data.get("deadline")):
            self.add_error("tracks_deadline", "Для панели рисков нужен рынок ДА/НЕТ с дедлайном.")
        options = [s.strip() for s in data.get("option_lines", "").splitlines() if s.strip()]
        if data.get("kind") in ("poll", "multiple"):
            if not 2 <= len(options) <= 20 or len(set(options)) != len(options) or any(len(s)>200 for s in options):
                self.add_error("option_lines", "Введите от 2 до 20 разных вариантов до 200 символов.")
        data["options"] = options
        if data.get("b") is not None and not 10 <= data["b"] <= 100000:
            self.add_error("b", "Ликвидность должна быть от 10 до 100000.")
        if data.get("deadline") and data["deadline"] <= timezone.now():
            self.add_error("deadline", "Укажите будущую дату.")
        if data.get("number_min") is not None and data.get("number_max") is not None:
            if data["number_min"] >= data["number_max"]:
                self.add_error("number_max", "Максимум должен быть больше минимума.")
        if data.get("tolerance") is not None and data["tolerance"] < 0:
            self.add_error("tolerance", "Ошибка не может быть отрицательной.")
        return data


class ResponseForm(forms.Form):
    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, **kwargs)
        if question.kind == "number":
            self.fields["value"] = forms.DecimalField(label="Ваше число", min_value=question.number_min, max_value=question.number_max, max_digits=16, decimal_places=4)
        else:
            choices = [(str(i), text) for i, text in enumerate(question.options)]
            field = forms.MultipleChoiceField if question.kind == "multiple" else forms.ChoiceField
            widget = forms.CheckboxSelectMultiple if question.kind == "multiple" else forms.RadioSelect
            self.fields["value"] = field(label="Варианты ответа", choices=choices, widget=widget)


class ResultForm(ResponseForm):
    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, question=question, **kwargs)
        if question.is_trading:
            self.fields["value"] = forms.ChoiceField(label="Результат", choices=[("yes", "ДА"), ("no", "НЕТ")])
        else:
            self.fields["value"].label = "Фактический результат"

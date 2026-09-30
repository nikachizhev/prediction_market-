from django.contrib import admin, messages

from . import services
from .models import Payout, Position, Question, Trade, Trader


@admin.register(Trader)
class TraderAdmin(admin.ModelAdmin):
    list_display = ("name", "pseudonym", "show_real_name", "github_login", "balance")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "source_type", "status", "outcome", "deadline")
    list_filter = ("status", "source_type")
    filter_horizontal = ("conflicted_traders",)
    def save_model(self, request, obj, form, change):
        if not change:
            from . import lmsr

            obj.q_yes, obj.q_no = lmsr.initial_q(obj.initial_prob, obj.b)
        super().save_model(request, obj, form, change)

    actions = ["resolve_yes", "resolve_no", "annul_selected"]

    def _apply(self, request, queryset, fn, label):
        for q in queryset:
            try:
                fn(q)
            except services.MarketError as e:
                self.message_user(request, f"{q.title}: {e}", messages.ERROR)
            else:
                self.message_user(request, f"{q.title}: {label}")

    @admin.action(description="Разрешить ДА")
    def resolve_yes(self, request, queryset):
        self._apply(request, queryset, lambda q: services.resolve(q, "yes"), "разрешён ДА")

    @admin.action(description="Разрешить НЕТ")
    def resolve_no(self, request, queryset):
        self._apply(request, queryset, lambda q: services.resolve(q, "no"), "разрешён НЕТ")

    @admin.action(description="Аннулировать")
    def annul_selected(self, request, queryset):
        self._apply(request, queryset, services.annul, "аннулирован")


for m in (Position, Trade, Payout):
    admin.site.register(m)

from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import PermissionDenied

from . import services
from .models import Payout, Position, Question, Trade, Trader, EmailVerification
from .question_forms import ResultForm
from .participation import finish
from .models import Group, GroupMembership, Notification


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "owner")
    search_fields = ("name",)
    readonly_fields = ("owner",)


@admin.register(GroupMembership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "trader", "status")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "created_at", "read_at")
    readonly_fields = ("recipient", "text", "kind", "question", "group", "key", "created_at")

admin.site.site_header = "Рынок прогнозов · Управление"
admin.site.site_title = "Админка рынка"
admin.site.index_title = "Пользователи, рынки и операции"


@admin.register(Trader)
class TraderAdmin(admin.ModelAdmin):
    list_display = ("name", "pseudonym", "user", "balance", "mascot")
    search_fields = ("name", "pseudonym", "user__username", "user__email")
    list_filter = ("mascot", "show_real_name")
    readonly_fields = ("balance", "telegram_chat_id", "user")
    list_select_related = ("user",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "source_type", "status", "outcome", "deadline", "result_link")
    list_filter = ("kind", "status", "source_type")
    search_fields = ("title", "resolution_criteria")
    readonly_fields = ("status", "outcome", "answer", "q_yes", "q_no", "pool_yes", "pool_no", "created_at", "resolved_at")
    actions = ["resolve_yes", "resolve_no", "close_selected", "annul_selected"]

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if obj:
            fields += ("group", "kind", "creator", "b", "alpha", "initial_prob", "options", "number_min", "number_max", "tolerance", "resolution_criteria", "source_ref", "source_type")
        return fields

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path("<int:pk>/result/", self.admin_site.admin_view(self.result_view), name="market_question_result")] + super().get_urls()

    @admin.display(description="Результат")
    def result_link(self, obj):
        from django.utils.html import format_html
        if obj.status in ("resolved", "annulled"):
            return "Завершён"
        return format_html('<a href="{}">Указать результат</a>', reverse("admin:market_question_result", args=[obj.pk]))

    def result_view(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        if not self.has_change_permission(request, question):
            raise PermissionDenied
        form = ResultForm(request.POST if request.method == "POST" else None, question=question)
        if request.method == "POST" and (question.kind == "poll" or form.is_valid()):
            try:
                finish(question, None, form.cleaned_data.get("value") if question.kind != "poll" else None, administrator=True)
            except services.MarketError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                self.log_change(request, question, "Результат опубликован через форму администратора")
                self.message_user(request, "Результат сохранён, выплаты выполнены.")
                return redirect("admin:market_question_changelist")
        return render(request, "admin/market_result.html", {**self.admin_site.each_context(request), "title": "Завершить вопрос", "question": question, "form": form})
    filter_horizontal = ("conflicted_traders",)
    def save_model(self, request, obj, form, change):
        if not change:
            from . import lmsr

            if obj.kind == "lmsr":
                obj.q_yes, obj.q_no = lmsr.initial_q(obj.initial_prob, obj.b)
            if obj.kind == "cpmm":
                obj.pool_yes = obj.pool_no = obj.b
        super().save_model(request, obj, form, change)

    def _apply(self, request, queryset, fn, label):
        for q in queryset:
            try:
                fn(q)
            except services.MarketError as e:
                self.message_user(request, f"{q.title}: {e}", messages.ERROR)
            else:
                self.log_change(request, q, label)
                self.message_user(request, f"{q.title}: {label}")

    @admin.action(description="Закрыть участие без определения результата")
    def close_selected(self, request, queryset):
        self._apply(request, queryset, services.close_betting, "участие закрыто")

    @admin.action(description="Разрешить ДА")
    def resolve_yes(self, request, queryset):
        self._apply(request, queryset, lambda q: services.resolve(q, "yes"), "разрешён ДА")

    @admin.action(description="Разрешить НЕТ")
    def resolve_no(self, request, queryset):
        self._apply(request, queryset, lambda q: services.resolve(q, "no"), "разрешён НЕТ")

    @admin.action(description="Аннулировать")
    def annul_selected(self, request, queryset):
        self._apply(request, queryset, services.annul, "аннулирован")


class ReadOnlyLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(Trade)
class TradeAdmin(ReadOnlyLedgerAdmin):
    list_display = ("trader", "question", "side", "shares", "cost", "created_at")
    list_filter = ("side", "question__kind")
    search_fields = ("trader__name", "question__title", "trader__user__email")
    list_select_related = ("trader", "question")


@admin.register(EmailVerification)
class VerificationAdmin(ReadOnlyLedgerAdmin):
    list_display = ("user", "email", "sent_at", "verified_at", "expires_at")
    search_fields = ("email", "user__username")
    list_filter = ("verified_at",)
    exclude = ("token_hash",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name != "token_hash"]


for m in (Position, Payout):
    admin.site.register(m, ReadOnlyLedgerAdmin)

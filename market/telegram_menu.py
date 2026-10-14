"""Private-chat navigation backed by the same data as the website."""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Question


BUTTONS = {
    "📊 Рынки": "/markets", "🎯 Мои ставки": "/positions",
    "📝 Мои вопросы": "/myquestions", "📈 Статистика": "/stats",
    "💰 Баланс": "/balance", "🌐 Войти на сайт": "/login",
    "❓ Помощь": "/help",
}


def keyboard():
    labels = list(BUTTONS)
    return {"keyboard": [[{"text": label} for label in labels[i:i+2]]
                         for i in range(0, len(labels), 2)],
            "resize_keyboard": True, "is_persistent": True}


def card(question):
    lines = [question.title, f"Тип: {question.get_kind_display()}",
             f"Статус: {question.get_status_display()}"]
    if question.is_trading:
        lines.append(f"Вероятность ДА: {question.prob:.1%}")
    if question.deadline:
        lines.append(f"Дедлайн: {timezone.localtime(question.deadline):%d.%m.%Y %H:%M} (МСК)")
    lines.append(f"{settings.SITE_URL}/q/{question.pk}/")
    return "\n".join(lines)


def handle_menu(trader, command, send):
    chat_id = trader.telegram_chat_id
    if command in ("/menu", "/start", "/help"):
        send(chat_id, "Выберите раздел кнопками ниже.\n"
             "Рынки — новые вопросы; мои ставки — ваши позиции и ответы; "
             "мои вопросы — созданные вами рынки.\n"
             "Участвовать и указывать результат можно на сайте по ссылке в карточке.\n"
             "/login — вход на сайт, /unlink — отключение Telegram.", reply_markup=keyboard())
    elif command == "/balance":
        from .services import portfolio_value, profit
        send(chat_id, f"Баланс: {trader.balance:.2f} монет.\n"
             f"Оценка открытых позиций: {portfolio_value(trader):.2f}\n"
             f"Прибыль с учётом позиций: {profit(trader):+.2f}\n"
             "Оценка позиций не гарантирует такую же сумму при продаже.", reply_markup=keyboard())
    elif command in ("/markets", "/myquestions"):
        from .access import visible_questions
        questions = visible_questions(trader)
        if command == "/markets":
            questions = questions.filter(status=Question.Status.OPEN).filter(
                Q(deadline__isnull=True) | Q(deadline__gt=timezone.now()))
            title, empty = "Новые открытые рынки", "Открытых рынков пока нет."
        else:
            questions = questions.filter(creator=trader)
            title, empty = "Мои вопросы", "Вы пока не создали вопросов."
        total = questions.count()
        send(chat_id, f"{title}: {total}. Показываю до 5 последних." if total else empty,
             reply_markup=keyboard())
        for question in questions.order_by("-created_at", "-pk")[:5]:
            send(chat_id, card(question))
        send(chat_id, f"Все вопросы и участие: {settings.SITE_URL}/markets/")
    elif command == "/positions":
        from .access import visible_questions
        positions = trader.positions.filter(question__in=visible_questions(trader),
            question__status__in=[Question.Status.OPEN, Question.Status.CLOSED],
        ).filter(Q(yes_shares__gt=0) | Q(no_shares__gt=0)).select_related("question")
        answers = trader.submissions.filter(question__in=visible_questions(trader),
            question__status__in=[Question.Status.OPEN, Question.Status.CLOSED],
        ).select_related("question")
        send(chat_id, f"Активных позиций: {positions.count()}. Ответов: {answers.count()}.",
             reply_markup=keyboard())
        for position in positions.order_by("-question__created_at")[:5]:
            send(chat_id, card(position.question) +
                 f"\nАкции ДА: {position.yes_shares:.2f}; НЕТ: {position.no_shares:.2f}"
                 f"\nВыплата при ДА: {position.yes_shares:.2f}; при НЕТ: {position.no_shares:.2f} монет.")
        for answer in answers.order_by("-created_at")[:5]:
            send(chat_id, card(answer.question) + f"\nВаш ответ: {answer.value}")
        send(chat_id, f"Все мои ставки: {settings.SITE_URL}/me/")
    elif command == "/stats":
        from .statistics import statistics, ranking
        stats = statistics(trader)
        rank = next((row["rank"] for row in ranking() if row["trader"].pk == trader.pk), None)
        accuracy = f'{stats["accuracy"]}%' if stats["accuracy"] is not None else "пока нет результатов"
        send(chat_id, f"Ваша статистика\nУчастий: {stats['markets']}\nСделок: {stats['trades']}\n"
             f"Верных прогнозов: {stats['correct']}; неверных: {stats['wrong']}\n"
             f"Точность: {accuracy}\nПрибыль: {stats['profit']:+.2f} монет\n"
             f"Выплаты: {stats['payouts']:.2f}\nМесто в рейтинге: {rank or '—'}\n"
             f"{settings.SITE_URL}/rating/", reply_markup=keyboard())
    else:
        return False
    return True

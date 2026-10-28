"""Similar open markets for the creation wizard, by the words of the question, without AI (spec E9.3).

A question becomes a set of rough stems: lower case, «ё» -> «е», punctuation and bare numbers dropped,
words shorter than three letters and stop words dropped, each word cut to its first five letters
(«сессию» and «сессия» meet). Two questions are as similar as the Dice coefficient of their stems.
Dates are stop words too: templates put «{date}» into most questions, so «декабря» says nothing
about what a market is about.
"""
from __future__ import annotations

import re
from fractions import Fraction

from django.db.models import Q
from django.utils import timezone

from .api.views import feed_markets
from .models import Question, Trader

MIN_QUERY = 10          # characters after strip, as in docs/api.md
MAX_QUERY = 300         # longer input is cut: titles are at most 120 characters anyway
MIN_WORD = 3
STEM = 5
CANDIDATES = 500        # newest open markets compared in Python
# Dice >= 0.35. One shared word is enough only for short questions (five meaningful words in both
# together, e.g. 2 + 3); two shared words — up to eleven together; «Сдам ли я сессию без троек?»
# and «Закроет ли больше половины потока сессию без троек?» share 2 of 3 + 5: 0.5.
THRESHOLD = Fraction(7, 20)

STOP_WORDS = frozenset(
    # particles, conjunctions, prepositions
    "ли ль бы же ж не ни и а но да нет или либо то тоже также даже лишь только уж уже еще вот ну "
    "в во на до по за из изо у к ко с со о об обо от ото для без при про над под перед через после "
    "между около вокруг среди кроме вместо против чем чтобы если хотя хоть раз пока "
    # question words and adverbs
    "кто что какой какая какое какие каким какую чей сколько почему зачем когда где куда откуда "
    "как так там тут здесь тогда теперь сейчас очень больше меньше более менее раньше позже "
    # pronouns
    "я меня мне мной мы нас нам нами ты тебя тебе тобой вы вас вам вами он его ему им ним него нему "
    "она ее ей нее ней оно они их них ими ними себя себе собой свой своя свое свои своего своей своих "
    "мой моя мое мои моего моей моих твой твоя твое твои наш наша наше наши нашего нашей наших нашим "
    "ваш ваша ваше ваши этот эта это эти этого этой этом этих этим тот та те того той том тех "
    "весь вся все всего всей всех сам сама само сами нибудь "
    # be / become
    "быть будет будут буду будем будешь будете был была было были есть станет стану станут станем стать "
    # dates
    "сегодня завтра послезавтра вчера день дня дней неделя недели неделе неделю месяц месяца месяце "
    "год года году лет конец конца конце начало начала начале середина середины числа "
    "январь января январе февраль февраля феврале март марта марте апрель апреля апреле май мая мае "
    "июнь июня июне июль июля июле август августа августе сентябрь сентября сентябре "
    "октябрь октября октябре ноябрь ноября ноябре декабрь декабря декабре "
    "понедельник понедельника понедельнику вторник вторника вторнику среда среды среду среде "
    "четверг четверга четвергу пятница пятницы пятницу пятнице суббота субботы субботу субботе "
    "воскресенье воскресенья "
    # a few English ones for titles like «Will GTA 6 come out»
    "the and for will with from".split()
)

_WORD = re.compile(r"[^\W_]+")


def stems(text: str) -> frozenset[str]:
    words = _WORD.findall((text or "").lower().replace("ё", "е"))
    return frozenset(w[:STEM] for w in words
                     if len(w) >= MIN_WORD and not w.isdigit() and w not in STOP_WORDS)


def similarity(a: frozenset[str], b: frozenset[str]) -> Fraction:
    if not a or not b:
        return Fraction(0)
    return Fraction(2 * len(a & b), len(a) + len(b))


def candidates(trader: Trader):
    """Open markets the trader sees and can still bet on, newest first."""
    now = timezone.now()
    return (feed_markets(trader).filter(status=Question.Status.OPEN)
            .filter(Q(deadline__isnull=True) | Q(deadline__gt=now))
            .order_by("-created_at", "-id"))


def similar_markets(trader: Trader, query: str, limit: int = 3) -> list[Question]:
    query = (query or "").strip()[:MAX_QUERY]
    if len(query) < MIN_QUERY:
        return []
    wanted = stems(query)
    if not wanted:
        return []
    rows = candidates(trader).values_list("pk", "title")[:CANDIDATES]
    scored = [(similarity(wanted, stems(title)), pk) for pk, title in rows]
    # Rows come newest first and the sort is stable: among equal scores the newer market stays on top.
    hits = sorted((pair for pair in scored if pair[0] >= THRESHOLD), key=lambda pair: pair[0], reverse=True)
    best = [pk for _, pk in hits[:limit]]
    found = Question.objects.in_bulk(best)
    return [found[pk] for pk in best if pk in found]

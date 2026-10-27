"""Charts for the bot (spec E6.2, E6.4): the 7-day probability animation and a static mini chart.

Colours follow the media bubble in docs/mockups/Bot.dc.html. Frames are drawn with matplotlib (Agg)
and piped to ffmpeg; without ffmpeg the bot answers with text instead.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ChartAnimation, Question

log = logging.getLogger(__name__)

MEDIA_BG = "#111418"
GRID = "#262B33"
LINE = "#7DB0FF"
INK = "#FFFFFF"
SUB = "#A9B1BB"
DOWN = "#FF9E6B"

WIDTH, HEIGHT = 480, 270      # even sizes for yuv420p
FPS = 20
DRAW_FRAMES = 44              # the line is drawn over 2.2 s ...
HOLD_FRAMES = 12              # ... then holds for 0.6 s: 2.8 s in total
SAMPLES = 85                  # points on the 7-day line


class ChartError(Exception):
    pass


def hour_of(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def week_series(question: Question, now: datetime | None = None) -> tuple[list[float], float]:
    """YES probability sampled evenly over the last 7 days, and the change over the last 24 h (p.p.)."""
    from .api.serializers import History
    now = now or timezone.now()
    since = now - timedelta(days=7)
    history = History(question, since=since)
    step = (now - since) / (SAMPLES - 1)
    probs = [history.at(since + step * i) for i in range(SAMPLES - 1)] + [question.prob]
    return probs, (question.prob - history.at(now - timedelta(days=1))) * 100


def delta_text(delta: float, *, suffix: str = " за сутки") -> str:
    points = round(delta)
    if points == 0:
        return "без изменений" + suffix
    return f"{'▲' if points > 0 else '▼'} {abs(points)}{suffix}"


def _figure(probs: list[float], delta: float):
    # Figure + Agg canvas directly: no pyplot, no global backend switch.
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = Figure(figsize=(WIDTH / 100, HEIGHT / 100), dpi=100, facecolor=MEDIA_BG)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.05, 0.16, 0.9, 0.58), facecolor=MEDIA_BG)
    ax.set_xlim(0, len(probs) - 1)
    ax.set_ylim(0, 1)
    for y in (0.25, 0.5, 0.75):
        ax.axhline(y, color=GRID, linewidth=1, zorder=0)
    ax.set_axis_off()
    fig.text(0.05, 0.86, f"{round(probs[-1] * 100)}%", color=INK, fontsize=26, fontweight="bold", va="center")
    fig.text(0.30, 0.86, delta_text(delta), color=LINE if round(delta) >= 0 else DOWN,
             fontsize=13, fontweight="bold", va="center")
    fig.text(0.05, 0.06, "7 дней назад", color=SUB, fontsize=10)
    fig.text(0.95, 0.06, "сейчас", color=SUB, fontsize=10, ha="right")
    line, = ax.plot([], [], color=LINE, linewidth=3, solid_capstyle="round", solid_joinstyle="round", clip_on=False)
    dot, = ax.plot([], [], "o", color=LINE, markersize=8, markeredgecolor=MEDIA_BG, markeredgewidth=2, clip_on=False)
    return fig, line, dot


def render_animation(probs: list[float], delta: float) -> bytes:
    """Silent H.264 MP4 (≤3 s) where the line is drawn progressively."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ChartError("ffmpeg не найден.")
    fig, line, dot = _figure(probs, delta)
    xs = list(range(len(probs)))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "chart.mp4"
        cmd = [ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-an",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            raise ChartError(f"ffmpeg не запустился: {exc}") from None
        try:
            for frame in range(DRAW_FRAMES + HOLD_FRAMES):
                share = min(1.0, (frame + 1) / DRAW_FRAMES)
                share = 1 - (1 - share) ** 2  # ease-out, as in the mockup
                n = max(2, round(share * len(probs)))
                line.set_data(xs[:n], probs[:n])
                dot.set_data([xs[n - 1]], [probs[n - 1]])
                fig.canvas.draw()
                proc.stdin.write(bytes(fig.canvas.buffer_rgba()))
            _, err = proc.communicate(timeout=60)  # closes stdin: end of stream
        except (OSError, subprocess.SubprocessError) as exc:
            proc.kill()
            raise ChartError(f"ffmpeg не справился: {exc}") from None
        if proc.returncode != 0 or not out.exists():
            raise ChartError(f"ffmpeg завершился с ошибкой: {err.decode(errors='replace')[:200]}")
        return out.read_bytes()


def render_still(probs: list[float], delta: float) -> bytes:
    """Static JPEG of the full line (inline photo results must be JPEG)."""
    fig, line, dot = _figure(probs, delta)
    xs = list(range(len(probs)))
    line.set_data(xs, probs)
    dot.set_data([xs[-1]], [probs[-1]])
    buffer = BytesIO()
    fig.savefig(buffer, format="jpeg", dpi=100, facecolor=MEDIA_BG, pil_kwargs={"quality": 88})
    return buffer.getvalue()


def animation_for(question: Question, now: datetime | None = None) -> ChartAnimation:
    """Cached animation for this market and hour; renders it on first use. Raises ChartError."""
    now = now or timezone.now()
    hour = hour_of(now)
    cached = ChartAnimation.objects.filter(question=question, hour=hour).first()
    if cached and (cached.file_id or cached.file):
        return cached
    probs, delta = week_series(question, now)
    data = render_animation(probs, delta)
    if cached is None:
        try:
            with transaction.atomic():
                cached = ChartAnimation.objects.create(question=question, hour=hour)
        except IntegrityError:
            # A parallel request rendered the same hour: use its row.
            cached = ChartAnimation.objects.get(question=question, hour=hour)
            if cached.file_id or cached.file:
                return cached
    cached.file.save(f"market-{question.pk}-{hour:%Y%m%d%H}.mp4", ContentFile(data), save=True)
    for old in ChartAnimation.objects.filter(question=question, hour__lt=hour):
        old.file.delete(save=False)
        old.delete()
    return cached

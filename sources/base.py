from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from market.models import Question


class SourceUpdate(enum.Enum):
    CLOSE_BETTING = "close_betting"
    RESOLVE_YES = "resolve_yes"
    RESOLVE_NO = "resolve_no"
    ANNUL = "annul"


@dataclass
class QuestionDraft:
    title: str
    resolution_criteria: str
    source_ref: str
    deadline: datetime | None = None
    initial_prob: float = 0.5
    conflicted_logins: list[str] = field(default_factory=list)  # GitHub logins
    conflicted_trader_ids: list[int] = field(default_factory=list)  # direct Trader pks


class QuestionSource(Protocol):
    source_type: str

    def discover(self) -> list[QuestionDraft]: ...

    def check(self, question: Question) -> SourceUpdate | None: ...

"""Работа с настройками времени выполнения (хранятся в таблице settings)."""
from __future__ import annotations

from .config import DEFAULT_WINNERS_COUNT, DRAW_DAY, DRAW_HOUR, DRAW_MINUTE
from .db import session_scope
from .models import Setting

KEY_WINNERS_COUNT = "winners_count"
KEY_DRAW_DAY = "draw_day"
KEY_DRAW_HOUR = "draw_hour"
KEY_DRAW_MINUTE = "draw_minute"


def get_setting(key: str, default: str | None = None) -> str | None:
    with session_scope() as s:
        row = s.get(Setting, key)
        return row.value if row else default


def set_setting(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))


def delete_setting(key: str) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row:
            s.delete(row)


def get_winners_count() -> int:
    raw = get_setting(KEY_WINNERS_COUNT)
    if raw is None:
        return DEFAULT_WINNERS_COUNT
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_WINNERS_COUNT


def set_winners_count(value: int) -> None:
    set_setting(KEY_WINNERS_COUNT, str(max(1, int(value))))


def get_draw_schedule() -> tuple[str, int, int]:
    """Расписание розыгрыша (day_of_week, hour, minute). Фолбэк — значения из .env."""
    day = get_setting(KEY_DRAW_DAY) or DRAW_DAY
    try:
        hour = int(get_setting(KEY_DRAW_HOUR)) if get_setting(KEY_DRAW_HOUR) else DRAW_HOUR
    except ValueError:
        hour = DRAW_HOUR
    try:
        minute = int(get_setting(KEY_DRAW_MINUTE)) if get_setting(KEY_DRAW_MINUTE) else DRAW_MINUTE
    except ValueError:
        minute = DRAW_MINUTE
    return day, hour, minute


def set_draw_schedule(day: str, hour: int, minute: int) -> None:
    set_setting(KEY_DRAW_DAY, day)
    set_setting(KEY_DRAW_HOUR, str(int(hour)))
    set_setting(KEY_DRAW_MINUTE, str(int(minute)))

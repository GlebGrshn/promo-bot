"""Конфигурация приложения. Все значения читаются из переменных окружения (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта и загрузка .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _ids(raw: str) -> list[int]:
    result: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


def _opt_int(raw: str | None) -> int | None:
    """Безопасный разбор необязательного числа (плейсхолдер/мусор -> None)."""
    try:
        return int((raw or "").strip())
    except (TypeError, ValueError):
        return None


# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# --- ВКонтакте ---
VK_GROUP_TOKEN: str = os.getenv("VK_GROUP_TOKEN", "").strip()

# --- МАКС ---
MAX_BOT_TOKEN: str = os.getenv("MAX_BOT_TOKEN", "").strip()
MAX_ADMIN_IDS: list[int] = _ids(os.getenv("MAX_ADMIN_IDS", ""))
MAX_ADMIN_CHAT_ID: int | None = _opt_int(os.getenv("MAX_ADMIN_CHAT_ID"))
# Владельцы (супер-админы) — доступ к панели управления.
# Основная переменная — MAX_OWNER_IDS (через запятую); MAX_OWNER_ID поддержан для совместимости.
MAX_OWNER_IDS: list[int] = _ids(os.getenv("MAX_OWNER_IDS", "")) or _ids(
    os.getenv("MAX_OWNER_ID", "")
)
# Первый владелец (обратная совместимость со старым кодом)
MAX_OWNER_ID: int | None = MAX_OWNER_IDS[0] if MAX_OWNER_IDS else None

# --- Операторы ---
ADMIN_IDS: list[int] = _ids(os.getenv("ADMIN_IDS", ""))
ADMIN_CHAT_ID: int | None = _opt_int(os.getenv("ADMIN_CHAT_ID")) or (
    ADMIN_IDS[0] if ADMIN_IDS else None
)

# --- Розыгрыш ---
DEFAULT_WINNERS_COUNT: int = _int("DEFAULT_WINNERS_COUNT", 3)
DRAW_DAY: str = os.getenv("DRAW_DAY", "mon").strip() or "mon"
DRAW_HOUR: int = _int("DRAW_HOUR", 10)
DRAW_MINUTE: int = _int("DRAW_MINUTE", 0)
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

# --- Хранилище ---
DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "data/bot.db")
MEDIA_DIR: Path = BASE_DIR / os.getenv("MEDIA_DIR", "data/media")

# Создаём нужные директории при импорте конфигурации
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def is_admin(user_id: int | str) -> bool:
    try:
        return int(user_id) in ADMIN_IDS
    except (TypeError, ValueError):
        return False

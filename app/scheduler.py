"""Планировщик еженедельного розыгрыша (каждый понедельник).

Импорты бот-фреймворков (aiogram / maxapi) — ленивые, внутри функций,
чтобы деплой под одну площадку не требовал зависимостей другой.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import services, settings
from .config import ADMIN_CHAT_ID, TIMEZONE

logger = logging.getLogger(__name__)

# ссылка на активный планировщик МАКС — для перепланирования из панели владельца
_max_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "weekly_draw"


def _trigger() -> CronTrigger:
    day, hour, minute = settings.get_draw_schedule()
    return CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=TIMEZONE)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
async def weekly_draw_job(bot) -> None:
    from .telegram_bot import send_candidate_or_summary

    if ADMIN_CHAT_ID is None:
        logger.warning("ADMIN_CHAT_ID не задан — розыгрыш запустить некому показать.")
        return
    if services.has_active_draw():
        await bot.send_message(ADMIN_CHAT_ID, "⏭ Авто-розыгрыш пропущен: предыдущий ещё не завершён.")
        return

    n = settings.get_winners_count()
    draw_id = services.start_draw(n)
    logger.info("Авто-розыгрыш (TG) #%s (победителей: %s)", draw_id, n)
    await bot.send_message(ADMIN_CHAT_ID, f"🗓 Понедельник — запускаю розыгрыш #{draw_id} (победителей: {n}).")
    await send_candidate_or_summary(bot, ADMIN_CHAT_ID, draw_id)


def build_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        weekly_draw_job, trigger=_trigger(), args=[bot],
        id="weekly_draw", replace_existing=True, misfire_grace_time=3600,
    )
    return scheduler


# ---------------------------------------------------------------------------
# МАКС
# ---------------------------------------------------------------------------
async def weekly_draw_job_max(bot) -> None:
    from .config import MAX_ADMIN_IDS, MAX_OWNER_IDS
    from .max_bot import get_review_chat, send_candidate_or_summary

    chat_id = get_review_chat()
    # Запасной вариант: чат ещё не запомнен — пишем напрямую первому оператору
    fallback_user = None
    if chat_id is None:
        candidates = MAX_ADMIN_IDS or MAX_OWNER_IDS
        if not candidates:
            logger.warning("MAX: некому показать розыгрыш — не задан ни один оператор.")
            return
        fallback_user = candidates[0]
        logger.info("MAX: чат не запомнен, шлю розыгрыш напрямую оператору %s", fallback_user)

    to = {"chat_id": chat_id} if chat_id is not None else {"user_id": int(fallback_user)}

    if services.has_active_draw():
        await bot.send_message(**to, text="⏭ Авто-розыгрыш пропущен: предыдущий ещё не завершён.")
        return

    n = settings.get_winners_count()
    draw_id = services.start_draw(n)
    logger.info("Авто-розыгрыш (MAX) #%s (победителей: %s)", draw_id, n)
    await bot.send_message(**to, text=f"🗓 Понедельник — запускаю розыгрыш #{draw_id} (победителей: {n}).")
    await send_candidate_or_summary(bot, chat_id, draw_id, user_id=fallback_user)


def build_scheduler_max(bot) -> AsyncIOScheduler:
    global _max_scheduler
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        weekly_draw_job_max, trigger=_trigger(), args=[bot],
        id=_JOB_ID, replace_existing=True, misfire_grace_time=3600,
    )
    _max_scheduler = scheduler
    return scheduler


def reschedule_max() -> bool:
    """Применяет текущее расписание из настроек к работающему планировщику МАКС."""
    if _max_scheduler is None:
        return False
    _max_scheduler.reschedule_job(_JOB_ID, trigger=_trigger())
    return True

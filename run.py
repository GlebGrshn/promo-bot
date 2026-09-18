"""Запуск всего сразу: Telegram-бот + ВК-бот + планировщик розыгрыша.

Запускаются только те боты, для которых задан токен в .env.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app import config, telegram_bot, vk_bot
from app.db import init_db
from app.scheduler import build_scheduler


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    _force_utf8()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("run")
    init_db()

    tasks = []

    if config.TELEGRAM_BOT_TOKEN:
        tg_bot = telegram_bot.build_bot()
        dp = telegram_bot.build_dispatcher()
        scheduler = build_scheduler(tg_bot)
        scheduler.start()
        log.info(
            "Планировщик: розыгрыш по расписанию %s %02d:%02d (%s)",
            config.DRAW_DAY, config.DRAW_HOUR, config.DRAW_MINUTE, config.TIMEZONE,
        )
        tasks.append(dp.start_polling(tg_bot))
    else:
        log.warning("TELEGRAM_BOT_TOKEN не задан — Telegram-бот и планировщик не запущены.")

    if config.VK_GROUP_TOKEN:
        vbot = vk_bot.build_bot()
        tasks.append(vbot.run_polling())
    else:
        log.warning("VK_GROUP_TOKEN не задан — ВК-бот не запущен.")

    if not tasks:
        raise SystemExit("Не задан ни один токен (TELEGRAM_BOT_TOKEN / VK_GROUP_TOKEN).")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

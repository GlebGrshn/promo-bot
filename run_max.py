"""Запуск бота для мессенджера МАКС + планировщик еженедельного розыгрыша.

Это основной способ запуска (площадка — только МАКС).
"""
from __future__ import annotations

import asyncio
import logging
import sys

from app import max_bot
from app.db import init_db
from app.scheduler import build_scheduler_max


async def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    init_db()

    bot = max_bot.build_bot()
    scheduler = build_scheduler_max(bot)
    scheduler.start()
    await max_bot.setup_commands(bot)
    logging.getLogger("run_max").info("МАКС-бот запущен, планировщик активен.")
    await max_bot.dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

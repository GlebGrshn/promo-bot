"""Запуск только Telegram-бота + планировщика розыгрыша."""
from __future__ import annotations

import asyncio
import logging
import sys

from app import telegram_bot
from app.db import init_db
from app.scheduler import build_scheduler


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
    bot = telegram_bot.build_bot()
    dp = telegram_bot.build_dispatcher()
    scheduler = build_scheduler(bot)
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

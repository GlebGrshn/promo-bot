"""ВКонтакте-бот: регистрация и приём чеков. Проверка чеков — у оператора в Telegram."""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import aiohttp

from . import services
from .config import MEDIA_DIR, VK_GROUP_TOKEN
from .models import PLATFORM_VK

logger = logging.getLogger(__name__)

# Простейшая FSM в памяти: peer_id -> {"step": str, "data": dict}
_STATES: dict[int, dict] = {}

STEP_NAME = "full_name"
STEP_CARD = "card"
STEP_PHONE = "phone"
STEP_CITY = "city"

START_WORDS = {"начать", "start", "/start", "привет", "старт"}


def _best_photo_url(photo) -> str | None:
    sizes = getattr(photo, "sizes", None) or []
    if not sizes:
        return None
    best = max(sizes, key=lambda s: (getattr(s, "height", 0) or 0) * (getattr(s, "width", 0) or 0))
    return getattr(best, "url", None)


async def _download(url: str, dest: Path) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                dest.write_bytes(await resp.read())
                return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось скачать фото ВК: %s", e)
        return False


async def _save_receipt_photos(peer_id: int, participant_id: int, attachments) -> int:
    saved = 0
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    for i, att in enumerate(attachments or []):
        photo = getattr(att, "photo", None)
        if not photo:
            continue
        url = _best_photo_url(photo)
        if not url:
            continue
        dest = MEDIA_DIR / f"vk_{peer_id}_{stamp}_{i}.jpg"
        if await _download(url, dest):
            services.add_receipt(participant_id, PLATFORM_VK, file_path=str(dest))
            saved += 1
    return saved


def build_bot():
    if not VK_GROUP_TOKEN:
        raise RuntimeError("VK_GROUP_TOKEN не задан в .env")

    from vkbottle.bot import Bot, Message

    bot = Bot(token=VK_GROUP_TOKEN)

    @bot.on.message()
    async def handle(message: Message) -> None:
        peer_id = message.peer_id
        from_id = message.from_id
        text = (message.text or "").strip()
        attachments = message.attachments or []
        state = _STATES.get(peer_id)

        # --- Идём по шагам регистрации ---
        if state:
            step = state["step"]
            if not text:
                await message.answer("Пожалуйста, ответьте текстом.")
                return

            if step == STEP_NAME:
                if len(text) < 3:
                    await message.answer("Пожалуйста, введите ФИО полностью.")
                    return
                state["data"]["full_name"] = text
                state["step"] = STEP_CARD
                await message.answer("Введите номер вашей бонусной карты:")
                return

            if step == STEP_CARD:
                if len(text) < 3:
                    await message.answer("Похоже, номер карты слишком короткий. Ещё раз?")
                    return
                state["data"]["card"] = text
                state["step"] = STEP_PHONE
                await message.answer("Введите ваш номер телефона:")
                return

            if step == STEP_PHONE:
                digits = "".join(ch for ch in text if ch.isdigit())
                if len(digits) < 10:
                    await message.answer("Похоже, это не номер телефона. Введите ещё раз.")
                    return
                state["data"]["phone"] = text
                state["step"] = STEP_CITY
                await message.answer("Из какого вы города?")
                return

            if step == STEP_CITY:
                if len(text) < 2:
                    await message.answer("Введите название города.")
                    return
                data = state["data"]
                _STATES.pop(peer_id, None)
                participant = services.register_participant(
                    platform=PLATFORM_VK,
                    platform_user_id=str(from_id),
                    full_name=data["full_name"],
                    card_number=data["card"],
                    phone=data["phone"],
                    city=text,
                )
                await message.answer(
                    f"✅ Готово! Ваш порядковый номер: {participant.display_number}\n\n"
                    "📸 Теперь присылайте фото чеков — каждый добавлю в розыгрыш. "
                    "Каждый понедельник среди новых чеков разыгрываются призы."
                )
                return
            return

        # --- Вне регистрации ---
        participant = services.get_participant(PLATFORM_VK, from_id)

        if participant is None:
            # Начинаем регистрацию
            _STATES[peer_id] = {"step": STEP_NAME, "data": {}}
            await message.answer(
                "Здравствуйте! Регистрирую вас в акции.\n\nНапишите ваше ФИО (полностью):"
            )
            return

        # Уже зарегистрирован
        if attachments and any(getattr(a, "photo", None) for a in attachments):
            saved = await _save_receipt_photos(peer_id, participant.id, attachments)
            if saved:
                await message.answer(f"🧾 Принял чеков: {saved}. Можете прислать ещё.")
            else:
                await message.answer("Не удалось сохранить фото. Пришлите чек ещё раз.")
            return

        if text.lower() in START_WORDS:
            await message.answer(
                f"Вы уже зарегистрированы под номером {participant.display_number}.\n"
                "📸 Присылайте фото чеков."
            )
            return

        await message.answer("📸 Пришлите фото чека — я его приму.")

    return bot


async def run_polling() -> None:
    bot = build_bot()
    logger.info("VK bot: старт polling")
    await bot.run_polling()

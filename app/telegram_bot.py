"""Telegram-бот: регистрация, приём чеков, обзор розыгрыша для оператора."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from . import export, services, settings
from .config import ADMIN_CHAT_ID, TELEGRAM_BOT_TOKEN, is_admin
from .models import PLATFORM_TG

logger = logging.getLogger(__name__)

user_router = Router()
admin_router = Router()


# ---------------------------------------------------------------------------
# Состояния регистрации
# ---------------------------------------------------------------------------
class Reg(StatesGroup):
    full_name = State()
    card = State()
    phone = State()
    city = State()


def _phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---------------------------------------------------------------------------
# Регистрация участника
# ---------------------------------------------------------------------------
@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    existing = services.get_participant(PLATFORM_TG, message.from_user.id)
    if existing:
        await message.answer(
            f"Вы уже зарегистрированы под номером <b>{existing.display_number}</b>.\n\n"
            "📸 Присылайте фото чеков — я приму каждый и добавлю в розыгрыш.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.set_state(Reg.full_name)
    await message.answer(
        "Здравствуйте! Регистрирую вас в акции.\n\n"
        "Напишите ваше <b>ФИО</b> (полностью):",
        reply_markup=ReplyKeyboardRemove(),
    )


@user_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Регистрация отменена. Наберите /start, чтобы начать заново.",
                         reply_markup=ReplyKeyboardRemove())


@user_router.message(Reg.full_name, F.text)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("Пожалуйста, введите ФИО полностью.")
        return
    await state.update_data(full_name=name)
    await state.set_state(Reg.card)
    await message.answer("Введите номер вашей <b>бонусной карты</b>:")


@user_router.message(Reg.card, F.text)
async def reg_card(message: Message, state: FSMContext) -> None:
    card = message.text.strip()
    if len(card) < 3:
        await message.answer("Похоже, номер карты слишком короткий. Попробуйте ещё раз.")
        return
    await state.update_data(card=card)
    await state.set_state(Reg.phone)
    await message.answer(
        "Введите ваш <b>номер телефона</b> или нажмите кнопку ниже 👇",
        reply_markup=_phone_keyboard(),
    )


@user_router.message(Reg.phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext) -> None:
    await _save_phone(message, state, message.contact.phone_number)


@user_router.message(Reg.phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text.strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        await message.answer("Похоже, это не номер телефона. Введите номер ещё раз.")
        return
    await _save_phone(message, state, phone)


async def _save_phone(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=phone)
    await state.set_state(Reg.city)
    await message.answer("Из какого вы <b>города</b>?", reply_markup=ReplyKeyboardRemove())


@user_router.message(Reg.city, F.text)
async def reg_city(message: Message, state: FSMContext) -> None:
    city = message.text.strip()
    if len(city) < 2:
        await message.answer("Введите название города.")
        return
    data = await state.get_data()
    await state.clear()

    participant = services.register_participant(
        platform=PLATFORM_TG,
        platform_user_id=str(message.from_user.id),
        full_name=data["full_name"],
        card_number=data["card"],
        phone=data["phone"],
        city=city,
    )
    await message.answer(
        f"✅ Готово! Ваш порядковый номер: <b>{participant.display_number}</b>\n\n"
        "📸 Теперь присылайте фото чеков. Каждый чек я приму и добавлю в розыгрыш.\n"
        "Каждый понедельник среди новых чеков разыгрываются призы.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ---------------------------------------------------------------------------
# Приём чеков (фото вне состояния регистрации)
# ---------------------------------------------------------------------------
@user_router.message(StateFilter(None), F.photo)
async def receipt_photo(message: Message) -> None:
    participant = services.get_participant(PLATFORM_TG, message.from_user.id)
    if not participant:
        await message.answer("Сначала зарегистрируйтесь — наберите /start.")
        return
    file_id = message.photo[-1].file_id  # самое большое разрешение
    services.add_receipt(participant.id, PLATFORM_TG, tg_file_id=file_id)
    await message.answer("🧾 Чек принят! Можете прислать ещё.")


@user_router.message(StateFilter(None), F.document)
async def receipt_document(message: Message) -> None:
    doc = message.document
    if not (doc.mime_type or "").startswith("image/"):
        await message.answer("Пришлите, пожалуйста, фото чека (как изображение).")
        return
    participant = services.get_participant(PLATFORM_TG, message.from_user.id)
    if not participant:
        await message.answer("Сначала зарегистрируйтесь — наберите /start.")
        return
    services.add_receipt(participant.id, PLATFORM_TG, tg_file_id=doc.file_id)
    await message.answer("🧾 Чек принят! Можете прислать ещё.")


@user_router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message) -> None:
    participant = services.get_participant(PLATFORM_TG, message.from_user.id)
    if participant:
        await message.answer("📸 Пришлите фото чека — я его приму.")
    else:
        await message.answer("Чтобы участвовать, зарегистрируйтесь — наберите /start.")


# ---------------------------------------------------------------------------
# Обзор розыгрыша для оператора
# ---------------------------------------------------------------------------
def _candidate_caption(c: services.CandidateView) -> str:
    p = c.participant
    src = "Telegram" if c.platform == PLATFORM_TG else "ВКонтакте"
    return (
        f"🎯 <b>Розыгрыш #{c.draw_id}</b>\n"
        f"Кандидат {c.position} из {c.pool_size} · площадка: {src}\n"
        f"Победителей найдено: <b>{c.winners_found}/{c.winners_target}</b>\n\n"
        f"Участник №<b>{p.display_number}</b>\n"
        f"ФИО: {p.full_name}\n"
        f"Город: {p.city}\n"
        f"Телефон: {p.phone}\n"
        f"Карта: {p.card_number}\n\n"
        "Проверьте в чеке нужный товар и сумму."
    )


def _candidate_keyboard(c: services.CandidateView) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🏆 Победитель",
                callback_data=f"draw:winner:{c.draw_id}:{c.receipt_id}",
            ),
            InlineKeyboardButton(
                text="➡️ Следующий",
                callback_data=f"draw:next:{c.draw_id}:{c.receipt_id}",
            ),
        ]]
    )


async def send_candidate_or_summary(bot: Bot, chat_id: int, draw_id: int) -> None:
    """Показывает оператору следующего кандидата или итог розыгрыша."""
    c = services.current_candidate(draw_id)
    if c is not None:
        caption = _candidate_caption(c)
        kb = _candidate_keyboard(c)
        if c.tg_file_id:
            await bot.send_photo(chat_id, photo=c.tg_file_id, caption=caption, reply_markup=kb)
        elif c.file_path:
            await bot.send_photo(
                chat_id, photo=FSInputFile(c.file_path), caption=caption, reply_markup=kb
            )
        else:
            await bot.send_message(
                chat_id,
                caption + "\n\n⚠️ Изображение чека недоступно.",
                reply_markup=kb,
            )
        return

    # Розыгрыш завершён — показываем итог
    result = services.draw_result(draw_id)
    if result is None:
        await bot.send_message(chat_id, "Розыгрыш не найден.")
        return
    if result.reason == "empty":
        await bot.send_message(
            chat_id,
            f"🗳 Розыгрыш #{result.draw_id}: новых чеков нет — разыгрывать нечего.",
        )
        return

    winners = services.list_winners(draw_id)
    lines = [
        f"🏁 <b>Розыгрыш #{result.draw_id} завершён</b>",
        f"Победителей: <b>{result.winners_found}/{result.winners_target}</b>"
        + (" (чеки закончились раньше)" if result.reason == "exhausted" else ""),
        "",
    ]
    if winners:
        lines.append("<b>Победители:</b>")
        for w in winners:
            src = "TG" if w.platform == PLATFORM_TG else "ВК"
            lines.append(f"• №{w.display_number} [{src}] {w.full_name}, {w.city}, {w.phone}")
    else:
        lines.append("Победители не отмечены.")
    await bot.send_message(chat_id, "\n".join(lines))


# --- Команды оператора ---
@admin_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>Панель оператора</b>\n\n"
        "/draw — запустить розыгрыш вручную\n"
        "/winners N — задать число победителей (сейчас: "
        f"{settings.get_winners_count()})\n"
        "/export — выгрузка всех участников в Excel\n"
        "/export_tg — выгрузка участников Telegram\n"
        "/export_vk — выгрузка участников ВКонтакте\n"
        "/stats — статистика\n"
        "/myid — показать ваш Telegram id"
    )


@admin_router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Ваш Telegram id: <code>{message.from_user.id}</code>")


@admin_router.message(Command("winners"))
async def cmd_winners(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            f"Сейчас разыгрывается победителей: <b>{settings.get_winners_count()}</b>.\n"
            "Чтобы изменить: <code>/winners 5</code>"
        )
        return
    try:
        n = int(parts[1])
        if n < 1:
            raise ValueError
    except ValueError:
        await message.answer("Укажите целое число ≥ 1, например: /winners 5")
        return
    settings.set_winners_count(n)
    await message.answer(f"✅ Число победителей на розыгрыш: <b>{n}</b>")


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    st = services.stats()
    await message.answer(
        "📊 <b>Статистика</b>\n"
        f"Участники Telegram: {st['participants_tg']}\n"
        f"Участники ВКонтакте: {st['participants_vk']}\n"
        f"Всего чеков: {st['receipts_total']}\n"
        f"Новых чеков (в очереди на розыгрыш): {st['receipts_pending']}"
    )


@admin_router.message(Command("draw"))
async def cmd_draw(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    if services.has_active_draw():
        await message.answer("Уже есть активный розыгрыш. Завершите его, прежде чем запускать новый.")
        return
    n = settings.get_winners_count()
    draw_id = services.start_draw(n)
    await message.answer(f"🚀 Запускаю розыгрыш #{draw_id} (победителей: {n}).")
    await send_candidate_or_summary(message.bot, message.chat.id, draw_id)


@admin_router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    await _do_export(message, platform=None)


@admin_router.message(Command("export_tg"))
async def cmd_export_tg(message: Message) -> None:
    await _do_export(message, platform="tg")


@admin_router.message(Command("export_vk"))
async def cmd_export_vk(message: Message) -> None:
    await _do_export(message, platform="vk")


async def _do_export(message: Message, platform: str | None) -> None:
    if not is_admin(message.from_user.id):
        return
    path = export.export_participants(platform)
    await message.answer_document(FSInputFile(path), caption="📥 Выгрузка участников")


@admin_router.callback_query(F.data.startswith("draw:"))
async def cb_draw(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для оператора.", show_alert=True)
        return
    try:
        _, action, draw_id_s, receipt_id_s = callback.data.split(":")
        draw_id, receipt_id = int(draw_id_s), int(receipt_id_s)
    except ValueError:
        await callback.answer()
        return

    if action == "winner":
        ok = services.mark_winner(draw_id, receipt_id, str(callback.from_user.id))
        note = "🏆 Отмечен победителем" if ok else "Уже обработано"
    else:
        ok = services.mark_next(draw_id, receipt_id, str(callback.from_user.id))
        note = "➡️ Пропущен" if ok else "Уже обработано"

    await callback.answer(note)
    # Убираем кнопки у обработанного сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass

    if ok:
        await send_candidate_or_summary(callback.bot, callback.message.chat.id, draw_id)


# ---------------------------------------------------------------------------
# Точка входа Telegram-бота
# ---------------------------------------------------------------------------
def build_bot() -> Bot:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    return Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)  # админские команды/колбэки — первыми
    dp.include_router(user_router)
    return dp


async def run_polling() -> None:
    bot = build_bot()
    dp = build_dispatcher()
    logger.info("Telegram bot: старт polling")
    await dp.start_polling(bot)

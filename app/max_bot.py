"""Бот для мессенджера МАКС (max.ru): регистрация, приём чеков, проверка оператором
и панель владельца (редактирование любых текстов + рассылка).

Использует библиотеку maxapi (стиль aiogram). Логика розыгрыша — общая, из app.services.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

import aiohttp
from maxapi import Bot, Dispatcher
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.types import (
    BotCommand,
    BotStarted,
    Command,
    InputMedia,
    MessageCallback,
    MessageCreated,
)
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.image import Image
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from . import export, scheduler, services, settings, texts
from .config import (
    MAX_ADMIN_CHAT_ID,
    MAX_ADMIN_IDS,
    MAX_BOT_TOKEN,
    MAX_OWNER_IDS,
    MEDIA_DIR,
)
from .models import PLATFORM_MAX

# Разбор дня недели для расписания розыгрыша
DAY_MAP = {
    "mon": "mon", "пн": "mon", "понедельник": "mon",
    "tue": "tue", "вт": "tue", "вторник": "tue",
    "wed": "wed", "ср": "wed", "среда": "wed",
    "thu": "thu", "чт": "thu", "четверг": "thu",
    "fri": "fri", "пт": "fri", "пятница": "fri",
    "sat": "sat", "сб": "sat", "суббота": "sat",
    "sun": "sun", "вс": "sun", "воскресенье": "sun",
}
DAY_RU = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}


def _parse_schedule(text: str) -> tuple[str, int, int] | None:
    """'пн 10:00' / 'mon 10:00' -> ('mon', 10, 0). None при ошибке."""
    parts = (text or "").lower().replace(",", " ").split()
    if len(parts) < 2:
        return None
    day = DAY_MAP.get(parts[0])
    if not day or ":" not in parts[1]:
        return None
    hh, mm = parts[1].split(":", 1)
    try:
        hour, minute = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return day, hour, minute

logger = logging.getLogger(__name__)

dp = Dispatcher()

KEY_REVIEW_CHAT = "max_review_chat"


# Состояние панели хранится отдельно для каждого владельца,
# чтобы несколько владельцев не мешали друг другу.
def _pending_key(uid) -> str:
    """Ожидаемый ввод владельца: 'winners' | 'broadcast' | 'schedule' | 'edit:<key>'."""
    return f"owner_pending:{uid}"


def _broadcast_key(uid) -> str:
    """Подготовленный текст рассылки конкретного владельца (до подтверждения)."""
    return f"pending_broadcast:{uid}"


# ---------------------------------------------------------------------------
# Роли и чат проверки
# ---------------------------------------------------------------------------
def is_owner(user_id) -> bool:
    try:
        return int(user_id) in MAX_OWNER_IDS
    except (TypeError, ValueError):
        return False


def is_admin(user_id) -> bool:
    try:
        return int(user_id) in MAX_ADMIN_IDS or is_owner(user_id)
    except (TypeError, ValueError):
        return False


def get_review_chat() -> int | None:
    if MAX_ADMIN_CHAT_ID is not None:
        return MAX_ADMIN_CHAT_ID
    raw = settings.get_setting(KEY_REVIEW_CHAT)
    return int(raw) if raw else None


def _remember_review_chat(chat_id) -> None:
    if MAX_ADMIN_CHAT_ID is None and chat_id is not None:
        settings.set_setting(KEY_REVIEW_CHAT, str(chat_id))


# ---------------------------------------------------------------------------
# Доступ к полям события
# ---------------------------------------------------------------------------
def _user_id(event) -> str:
    return str(event.message.sender.user_id)


def _chat_id(event):
    return event.message.recipient.chat_id


def _text(event) -> str | None:
    body = event.message.body
    if body and body.text:
        return body.text.strip()
    return None


# ---------------------------------------------------------------------------
# Состояния регистрации участника
# ---------------------------------------------------------------------------
class Reg(StatesGroup):
    full_name = State()
    card = State()
    phone = State()
    city = State()


async def _begin_registration(event, context: MemoryContext) -> None:
    await context.set_state(Reg.full_name)
    await event.message.answer(texts.get("ask_name"))


# ---------------------------------------------------------------------------
# Главное меню (кнопки под сообщением)
# ---------------------------------------------------------------------------
def _main_menu_kb(user_id):
    """Кнопки главного меню. Состав зависит от роли и регистрации."""
    part = services.get_participant(PLATFORM_MAX, str(user_id))
    b = InlineKeyboardBuilder()
    if part is None:
        b.row(CallbackButton(text="📝 Участвовать в акции", payload="m:reg"))
    else:
        b.row(CallbackButton(text="🧾 Отправить чек", payload="m:receipt"))
        b.row(
            CallbackButton(text="🔢 Мой номер", payload="m:me"),
            CallbackButton(text="❓ Как участвовать", payload="m:help"),
        )
    if is_admin(user_id):
        b.row(CallbackButton(text="🎯 Запустить розыгрыш", payload="m:draw"))
        b.row(
            CallbackButton(text="📊 Статистика", payload="m:stats"),
            CallbackButton(text="📥 Выгрузка Excel", payload="m:export"),
        )
    if is_owner(user_id):
        b.row(CallbackButton(text="🛠 Панель владельца", payload="m:panel"))
    return b.as_markup()


async def _send_main_menu(event, user_id, greeting: str | None = None) -> None:
    part = services.get_participant(PLATFORM_MAX, str(user_id))
    if greeting:
        text = greeting
    elif part:
        text = texts.get("menu_registered", number=part.display_number)
    else:
        text = texts.get("menu_welcome")
    await event.message.answer(text, attachments=[_main_menu_kb(user_id)])


# ---------------------------------------------------------------------------
# Пользователь открыл бота (кнопка «Начать») — автоматический старт
# ---------------------------------------------------------------------------
@dp.bot_started()
async def on_bot_started(event: BotStarted, context: MemoryContext) -> None:
    await context.clear()
    uid = str(event.user.user_id) if event.user else None
    if uid is None:
        return
    part = services.get_participant(PLATFORM_MAX, uid)
    text = (
        texts.get("menu_registered", number=part.display_number)
        if part else texts.get("menu_welcome")
    )
    await event.bot.send_message(
        chat_id=event.chat_id, text=text, attachments=[_main_menu_kb(uid)]
    )


# ---------------------------------------------------------------------------
# Команды участника / оператора
# ---------------------------------------------------------------------------
@dp.message_created(Command("start"))
async def cmd_start(event: MessageCreated, context: MemoryContext) -> None:
    await context.clear()
    await _send_main_menu(event, _user_id(event))


@dp.message_created(Command("menu"))
async def cmd_menu(event: MessageCreated, context: MemoryContext) -> None:
    await context.clear()
    await _send_main_menu(event, _user_id(event))


@dp.message_created(Command("help"))
async def cmd_help(event: MessageCreated) -> None:
    await event.message.answer(texts.get("help"), attachments=[_main_menu_kb(_user_id(event))])


@dp.message_created(Command("myid"))
async def cmd_myid(event: MessageCreated) -> None:
    await event.message.answer(f"Ваш MAX user_id: {_user_id(event)}")


@dp.message_created(Command("admin"))
async def cmd_admin(event: MessageCreated) -> None:
    if not is_admin(_user_id(event)):
        return
    _remember_review_chat(_chat_id(event))
    extra = "\n/panel — панель владельца" if is_owner(_user_id(event)) else ""
    await event.message.answer(
        "Панель оператора\n\n"
        "/draw — запустить розыгрыш вручную\n"
        f"/winners N — число победителей (сейчас: {settings.get_winners_count()})\n"
        "/export — выгрузка участников в Excel\n"
        "/stats — статистика\n"
        "/myid — показать ваш MAX user_id" + extra
    )


@dp.message_created(Command("winners"))
async def cmd_winners(event: MessageCreated) -> None:
    if not is_admin(_user_id(event)):
        return
    parts = ((event.message.body.text if event.message.body else "") or "").split()
    if len(parts) < 2:
        await event.message.answer(
            f"Сейчас победителей: {settings.get_winners_count()}. Изменить: /winners 5"
        )
        return
    try:
        n = int(parts[1])
        if n < 1:
            raise ValueError
    except ValueError:
        await event.message.answer("Укажите целое число ≥ 1, например: /winners 5")
        return
    settings.set_winners_count(n)
    await event.message.answer(f"✅ Число победителей на розыгрыш: {n}")


@dp.message_created(Command("stats"))
async def cmd_stats(event: MessageCreated) -> None:
    if not is_admin(_user_id(event)):
        return
    st = services.stats()
    await event.message.answer(
        "📊 Статистика\n"
        f"Участники МАКС: {st.get('participants_max', 0)}\n"
        f"Всего чеков: {st['receipts_total']}\n"
        f"Новых чеков (в очереди на розыгрыш): {st['receipts_pending']}"
    )


@dp.message_created(Command("export"))
async def cmd_export(event: MessageCreated) -> None:
    if not is_admin(_user_id(event)):
        return
    path = export.export_participants(PLATFORM_MAX)
    await event.message.answer(
        text="📥 Выгрузка участников", attachments=[InputMedia(path=str(path))]
    )


@dp.message_created(Command("draw"))
async def cmd_draw(event: MessageCreated) -> None:
    if not is_admin(_user_id(event)):
        return
    _remember_review_chat(_chat_id(event))
    if services.has_active_draw():
        await event.message.answer(
            "Уже есть активный розыгрыш. Завершите его, прежде чем запускать новый."
        )
        return
    n = settings.get_winners_count()
    draw_id = services.start_draw(n)
    await event.message.answer(f"🚀 Запускаю розыгрыш #{draw_id} (победителей: {n}).")
    await send_candidate_or_summary(event.bot, _chat_id(event), draw_id)


# ---------------------------------------------------------------------------
# Панель владельца
# ---------------------------------------------------------------------------
def _panel_menu_text() -> str:
    return "🛠 Панель владельца\nВыберите раздел:"


def _panel_menu_kb():
    day, hour, minute = settings.get_draw_schedule()
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="✏️ Тексты бота", payload="ptexts"))
    b.row(CallbackButton(text=f"🏆 Победителей: {settings.get_winners_count()}", payload="pwin"))
    b.row(CallbackButton(
        text=f"🗓 Розыгрыш: {DAY_RU.get(day, day)} {hour:02d}:{minute:02d}", payload="psch"
    ))
    b.row(CallbackButton(text="📣 Рассылка участникам", payload="pbc"))
    b.row(CallbackButton(text="⬅️ В главное меню", payload="m:menu"))
    return b.as_markup()


def _cancel_kb(back_payload: str = "pmenu"):
    """Кнопка отмены для экранов, где бот ждёт ввод текста."""
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="⬅️ Отмена", payload=back_payload))
    return b.as_markup()


def _texts_submenu_kb():
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="👤 Тексты участнику", payload="ptxg:user"))
    b.row(CallbackButton(text="🛠 Тексты оператору", payload="ptxg:operator"))
    b.row(CallbackButton(text="⬅️ Назад", payload="pmenu"))
    return b.as_markup()


def _texts_list_kb(category: str):
    b = InlineKeyboardBuilder()
    for key in texts.keys(category):
        mark = " ✎" if texts.is_overridden(key) else ""
        b.row(CallbackButton(text=texts.title(key) + mark, payload=f"ptxt:{key}"))
    b.row(CallbackButton(text="⬅️ Назад", payload="ptexts"))
    return b.as_markup()


def _text_detail(key: str) -> str:
    status = "изменён" if texts.is_overridden(key) else "по умолчанию"
    ph = texts.placeholders(key)
    hint = ("\n\nПодстановки: " + ", ".join("{" + p + "}" for p in ph)) if ph else ""
    return f"«{texts.title(key)}» ({status}):\n\n{texts.raw(key)}{hint}"


def _text_detail_kb(key: str):
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="✏️ Изменить текст", payload=f"ptxe:{key}"))
    if texts.is_overridden(key):
        b.row(CallbackButton(text="↩️ Сбросить к стандартному", payload=f"ptxr:{key}"))
    b.row(CallbackButton(text="⬅️ К списку", payload=f"ptxg:{texts.category(key)}"))
    return b.as_markup()


def _broadcast_confirm_kb():
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="✅ Отправить всем", payload="bcyes"),
        CallbackButton(text="✖️ Отмена", payload="bcno"),
    )
    return b.as_markup()


@dp.message_created(Command("panel"))
async def cmd_panel(event: MessageCreated, context: MemoryContext) -> None:
    if not is_owner(_user_id(event)):
        return
    await context.clear()
    settings.delete_setting(_pending_key(_user_id(event)))
    _remember_review_chat(_chat_id(event))
    await event.message.answer(_panel_menu_text(), attachments=[_panel_menu_kb()])


async def _panel_callback(event: MessageCallback, head: str, arg: str, uid) -> None:
    # Навигация (сбрасываем ожидаемый ввод)
    if head == "pmenu":
        settings.delete_setting(_pending_key(uid))
        await event.answer(new_text=_panel_menu_text(), attachments=[_panel_menu_kb()])
    elif head == "ptexts":
        settings.delete_setting(_pending_key(uid))
        await event.answer(
            new_text="✏️ Тексты бота — выберите раздел:", attachments=[_texts_submenu_kb()]
        )
    elif head == "ptxg":
        settings.delete_setting(_pending_key(uid))
        cat_title = "участнику" if arg == "user" else "оператору"
        await event.answer(
            new_text=f"Тексты {cat_title} — что изменить?", attachments=[_texts_list_kb(arg)]
        )
    elif head == "ptxt":
        settings.delete_setting(_pending_key(uid))
        await event.answer(new_text=_text_detail(arg), attachments=[_text_detail_kb(arg)])
    # Действия (ставим ожидаемый ввод)
    elif head == "ptxe":
        settings.set_setting(_pending_key(uid), f"edit:{arg}")
        await event.answer(
            new_text=f"Пришлите новый текст для «{texts.title(arg)}» одним сообщением."
            + (
                "\n\nСохраните подстановки: " + ", ".join("{" + p + "}" for p in texts.placeholders(arg))
                if texts.placeholders(arg)
                else ""
            ),
            attachments=[_cancel_kb(f"ptxt:{arg}")],
        )
    elif head == "ptxr":
        texts.reset(arg)
        settings.delete_setting(_pending_key(uid))
        await event.answer(
            notification="Сброшено к стандартному тексту.",
            new_text=_text_detail(arg),
            attachments=[_text_detail_kb(arg)],
        )
    elif head == "pwin":
        settings.set_setting(_pending_key(uid), "winners")
        await event.answer(
            new_text=f"Сейчас победителей на розыгрыш: {settings.get_winners_count()}.\n"
            "Пришлите новое число одним сообщением (например, 5).",
            attachments=[_cancel_kb()],
        )
    elif head == "psch":
        settings.set_setting(_pending_key(uid), "schedule")
        day, hour, minute = settings.get_draw_schedule()
        await event.answer(
            new_text=f"🗓 Сейчас розыгрыш: {DAY_RU.get(day, day)} {hour:02d}:{minute:02d}.\n\n"
            "Пришлите новый день и время одним сообщением, например:\n"
            "«пн 10:00» или «fri 18:30».",
            attachments=[_cancel_kb()],
        )
    elif head == "pbc":
        settings.set_setting(_pending_key(uid), "broadcast")
        await event.answer(
            new_text="📣 Пришлите текст рассылки одним сообщением.\n"
            "Он будет отправлен всем участникам МАКС (после подтверждения).",
            attachments=[_cancel_kb()],
        )
    elif head == "bcyes":
        await _do_broadcast(event, uid)
    elif head == "bcno":
        settings.delete_setting(_broadcast_key(uid))
        settings.delete_setting(_pending_key(uid))
        await event.answer(
            notification="Рассылка отменена.",
            new_text="📣 Рассылка отменена.",
            attachments=[_panel_menu_kb()],
        )


async def _do_broadcast(event: MessageCallback, uid) -> None:
    text = settings.get_setting(_broadcast_key(uid))
    if not text:
        await event.answer(notification="Нет подготовленного текста рассылки.")
        return
    settings.delete_setting(_broadcast_key(uid))
    await event.answer(new_text="📣 Рассылка запущена, подождите…")

    user_ids = services.all_participant_user_ids(PLATFORM_MAX)
    ok = fail = 0
    for u in user_ids:
        try:
            await event.bot.send_message(user_id=int(u), text=text)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            logger.warning("Рассылка: не доставлено %s: %s", u, e)
        await asyncio.sleep(0.05)  # мягкое ограничение частоты

    owner = getattr(event.callback.user, "user_id", None) if event.callback.user else None
    if owner is not None:
        await event.bot.send_message(
            user_id=int(owner),
            text=f"✅ Рассылка завершена. Доставлено: {ok}, ошибок: {fail}.",
            attachments=[_panel_menu_kb()],
        )


async def _handle_owner_input(event: MessageCreated, pending: str, uid: str) -> None:
    """Обрабатывает следующее сообщение владельца после нажатия кнопки в панели."""
    text = event.message.body.text if event.message.body else None
    settings.delete_setting(_pending_key(uid))

    if pending == "winners":
        try:
            n = int((text or "").strip())
            if n < 1:
                raise ValueError
        except ValueError:
            await event.message.answer(
                "Нужно целое число ≥ 1.", attachments=[_panel_menu_kb()]
            )
            return
        settings.set_winners_count(n)
        await event.message.answer(f"✅ Число победителей: {n}", attachments=[_panel_menu_kb()])
        return

    if pending == "schedule":
        parsed = _parse_schedule(text or "")
        if not parsed:
            await event.message.answer(
                "Не понял формат. Пример: «пн 10:00» или «fri 18:30».",
                attachments=[_panel_menu_kb()],
            )
            return
        day, hour, minute = parsed
        settings.set_draw_schedule(day, hour, minute)
        scheduler.reschedule_max()  # применяем к работающему планировщику
        await event.message.answer(
            f"✅ Розыгрыш теперь: {DAY_RU.get(day, day)} {hour:02d}:{minute:02d}.",
            attachments=[_panel_menu_kb()],
        )
        return

    if pending == "broadcast":
        if not text or not text.strip():
            await event.message.answer(
                "Текст пустой — рассылка отменена.", attachments=[_panel_menu_kb()]
            )
            return
        settings.set_setting(_broadcast_key(uid), text)
        count = len(services.all_participant_user_ids(PLATFORM_MAX))
        await event.message.answer(
            f"📣 Предпросмотр рассылки:\n\n{text}\n\nОтправить {count} участникам?",
            attachments=[_broadcast_confirm_kb()],
        )
        return

    if pending.startswith("edit:"):
        key = pending.split(":", 1)[1]
        if key not in texts.DEFAULTS:
            await event.message.answer(
                "Неизвестный текст.", attachments=[_panel_menu_kb()]
            )
            return
        if not text:
            await event.message.answer("Пришлите новый текст сообщением.")
            settings.set_setting(_pending_key(uid), pending)  # ждём ещё раз
            return
        texts.set_text(key, text)
        await event.message.answer(
            f"✅ Текст «{texts.title(key)}» обновлён.", attachments=[_text_detail_kb(key)]
        )
        return


# ---------------------------------------------------------------------------
# Регистрация (пошагово)
# ---------------------------------------------------------------------------
@dp.message_created(Reg.full_name)
async def reg_full_name(event: MessageCreated, context: MemoryContext) -> None:
    text = _text(event)
    if not text or len(text) < 3:
        await event.message.answer(texts.get("err_name"))
        return
    await context.update_data(full_name=text)
    await context.set_state(Reg.card)
    await event.message.answer(texts.get("ask_card"))


@dp.message_created(Reg.card)
async def reg_card(event: MessageCreated, context: MemoryContext) -> None:
    text = _text(event)
    if not text or len(text) < 3:
        await event.message.answer(texts.get("err_card"))
        return
    await context.update_data(card=text)
    await context.set_state(Reg.phone)
    await event.message.answer(texts.get("ask_phone"))


@dp.message_created(Reg.phone)
async def reg_phone(event: MessageCreated, context: MemoryContext) -> None:
    text = _text(event)
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    if len(digits) < 10:
        await event.message.answer(texts.get("err_phone"))
        return
    await context.update_data(phone=text)
    await context.set_state(Reg.city)
    await event.message.answer(texts.get("ask_city"))


@dp.message_created(Reg.city)
async def reg_city(event: MessageCreated, context: MemoryContext) -> None:
    text = _text(event)
    if not text or len(text) < 2:
        await event.message.answer(texts.get("err_city"))
        return
    data = await context.get_data()
    await context.clear()
    part = services.register_participant(
        platform=PLATFORM_MAX,
        platform_user_id=_user_id(event),
        full_name=data.get("full_name", ""),
        card_number=data.get("card", ""),
        phone=data.get("phone", ""),
        city=text,
    )
    await event.message.answer(
        texts.get("registered", number=part.display_number),
        attachments=[_main_menu_kb(_user_id(event))],
    )


# ---------------------------------------------------------------------------
# Приём чеков и прочие сообщения (вне регистрации)
# ---------------------------------------------------------------------------
async def _save_receipt_images(uid: str, participant_id: int, images: list) -> int:
    saved = 0
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    async with aiohttp.ClientSession() as session:
        for i, img in enumerate(images):
            url = getattr(img.payload, "url", None) if img.payload else None
            if not url:
                continue
            dest = MEDIA_DIR / f"max_{uid}_{stamp}_{i}.jpg"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        dest.write_bytes(await resp.read())
                        services.add_receipt(participant_id, PLATFORM_MAX, file_path=str(dest))
                        saved += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Не удалось скачать фото МАКС: %s", e)
    return saved


@dp.message_created(None)
async def catch_all(event: MessageCreated, context: MemoryContext) -> None:
    body = event.message.body
    uid = _user_id(event)

    # команды обрабатываются своими хендлерами — здесь пропускаем
    if body and body.text and body.text.strip().startswith("/"):
        return

    # Владелец: перехватываем ввод для панели, в регистрацию не вовлекаем
    if is_owner(uid):
        pending = settings.get_setting(_pending_key(uid))
        if pending:
            await _handle_owner_input(event, pending, uid)
            return
        # без ожидаемого ввода владелец пользуется ботом как обычный участник
        if not (body and body.attachments) and not services.get_participant(PLATFORM_MAX, uid):
            await _send_main_menu(event, uid)
            return

    attachments = (body.attachments if body else None) or []
    images = [a for a in attachments if isinstance(a, Image)]
    part = services.get_participant(PLATFORM_MAX, uid)

    if images:
        if not part:
            await _send_main_menu(event, uid)
            return
        saved = await _save_receipt_images(uid, part.id, images)
        if saved:
            await event.message.answer(
                texts.get("receipt_accepted", count=saved),
                attachments=[_main_menu_kb(uid)],
            )
        else:
            await event.message.answer(texts.get("receipt_failed"))
        return

    # Незарегистрированному показываем меню с кнопкой «Участвовать»
    if not part:
        await _send_main_menu(event, uid)
        return

    await event.message.answer(
        texts.get("send_receipt_prompt"), attachments=[_main_menu_kb(uid)]
    )


# ---------------------------------------------------------------------------
# Проверка чеков оператором
# ---------------------------------------------------------------------------
def _candidate_caption(c: services.CandidateView) -> str:
    p = c.participant
    return texts.get(
        "op_candidate",
        draw_id=c.draw_id,
        position=c.position,
        pool_size=c.pool_size,
        winners_found=c.winners_found,
        winners_target=c.winners_target,
        number=p.display_number,
        full_name=p.full_name,
        city=p.city,
        phone=p.phone,
        card_number=p.card_number,
    )


def _candidate_keyboard(c: services.CandidateView):
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text=texts.get("op_btn_winner"), payload=f"winner:{c.draw_id}:{c.receipt_id}"),
        CallbackButton(text=texts.get("op_btn_next"), payload=f"next:{c.draw_id}:{c.receipt_id}"),
    )
    return builder.as_markup()


def _target(chat_id, user_id=None) -> dict:
    """Куда слать: в чат (обычный случай) или напрямую пользователю (запасной)."""
    if chat_id is not None:
        return {"chat_id": chat_id}
    return {"user_id": int(user_id)}


async def send_candidate_or_summary(bot: Bot, chat_id, draw_id: int, user_id=None) -> None:
    to = _target(chat_id, user_id)
    c = services.current_candidate(draw_id)
    if c is not None:
        attachments = []
        if c.file_path and Path(c.file_path).exists():
            attachments.append(InputMedia(path=c.file_path))
        attachments.append(_candidate_keyboard(c))
        await bot.send_message(**to, text=_candidate_caption(c), attachments=attachments)
        return

    result = services.draw_result(draw_id)
    if result is None:
        await bot.send_message(**to, text="Розыгрыш не найден.")
        return
    if result.reason == "empty":
        await bot.send_message(**to, text=texts.get("op_empty_draw", draw_id=result.draw_id))
        return

    winners = services.list_winners(draw_id)
    header = texts.get(
        "op_draw_finished",
        draw_id=result.draw_id,
        winners_found=result.winners_found,
        winners_target=result.winners_target,
    )
    if result.reason == "exhausted":
        header += " (чеки закончились раньше)"
    lines = [header, ""]
    if winners:
        lines.append("Победители:")
        for w in winners:
            lines.append(texts.get(
                "op_winner_line",
                number=w.display_number,
                full_name=w.full_name,
                city=w.city,
                phone=w.phone,
            ))
    else:
        lines.append("Победители не отмечены.")
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def _menu_callback(event: MessageCallback, action: str, context: MemoryContext) -> None:
    """Кнопки главного меню."""
    uid = str(event.callback.user.user_id) if event.callback.user else None
    part = services.get_participant(PLATFORM_MAX, uid) if uid else None

    if action == "menu":
        if uid:
            settings.delete_setting(_pending_key(uid))
        text = (
            texts.get("menu_registered", number=part.display_number)
            if part else texts.get("menu_welcome")
        )
        await event.answer(new_text=text, attachments=[_main_menu_kb(uid)])
        return

    if action == "reg":
        if part:
            await event.answer(new_text=texts.get("already_registered", number=part.display_number))
            return
        await context.set_state(Reg.full_name)
        await event.answer(new_text=texts.get("ask_name"))
        return

    if action == "receipt":
        await event.answer(new_text=texts.get("send_receipt_prompt"))
        return

    if action == "help":
        await event.answer(new_text=texts.get("help"))
        return

    if action == "me":
        if not part:
            await event.answer(notification="Вы ещё не зарегистрированы.")
            return
        await event.answer(new_text=texts.get(
            "my_number",
            number=part.display_number,
            full_name=part.full_name,
            city=part.city,
            phone=part.phone,
            receipts=services.count_participant_receipts(part.id),
        ))
        return

    # --- Действия оператора ---
    if action in ("draw", "stats", "export") and not is_admin(uid):
        await event.answer(notification="Только для оператора.")
        return

    if action == "stats":
        st = services.stats()
        await event.answer(new_text=(
            "📊 Статистика\n"
            f"Участники МАКС: {st.get('participants_max', 0)}\n"
            f"Всего чеков: {st['receipts_total']}\n"
            f"Новых чеков (в очереди): {st['receipts_pending']}"
        ))
        return

    if action == "export":
        path = export.export_participants(PLATFORM_MAX)
        await event.answer(notification="Готовлю выгрузку…")
        await event.bot.send_message(
            chat_id=event.message.recipient.chat_id,
            text="📥 Выгрузка участников",
            attachments=[InputMedia(path=str(path))],
        )
        return

    if action == "draw":
        chat_id = event.message.recipient.chat_id
        _remember_review_chat(chat_id)
        if services.has_active_draw():
            await event.answer(notification="Уже есть активный розыгрыш.")
            return
        n = settings.get_winners_count()
        draw_id = services.start_draw(n)
        await event.answer(new_text=f"🚀 Запускаю розыгрыш #{draw_id} (победителей: {n}).")
        await send_candidate_or_summary(event.bot, chat_id, draw_id)
        return

    if action == "panel":
        if not is_owner(uid):
            await event.answer(notification="Только для владельца.")
            return
        settings.delete_setting(_pending_key(uid))
        await event.answer(new_text=_panel_menu_text(), attachments=[_panel_menu_kb()])
        return


async def _notify_winner(bot: Bot, receipt_id: int) -> tuple[bool, str]:
    """Отправляет победителю уведомление в МАКС. Возвращает (успех, номер участника)."""
    p = services.receipt_participant(receipt_id)
    if not p:
        return False, "?"
    if p.platform != PLATFORM_MAX or not p.platform_user_id:
        return False, p.display_number
    try:
        await bot.send_message(
            user_id=int(p.platform_user_id),
            text=texts.get("win_notify", number=p.display_number, full_name=p.full_name),
        )
        return True, p.display_number
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось уведомить победителя %s: %s", p.platform_user_id, e)
        return False, p.display_number


@dp.message_callback()
async def on_callback(event: MessageCallback, context: MemoryContext) -> None:
    payload = event.callback.payload or ""
    uid = getattr(event.callback.user, "user_id", None) if event.callback.user else None
    head = payload.split(":", 1)[0]
    arg = payload.split(":", 1)[1] if ":" in payload else ""

    # --- Кнопки главного меню ---
    if head == "m":
        await _menu_callback(event, arg, context)
        return

    # --- Кнопки панели владельца ---
    if head in (
        "pmenu", "ptexts", "ptxg", "ptxt", "ptxe", "ptxr",
        "pwin", "psch", "pbc", "bcyes", "bcno",
    ):
        if not is_owner(uid):
            await event.answer(notification="Только для владельца.")
            return
        await _panel_callback(event, head, arg, uid)
        return

    # --- Кнопки проверки чеков (оператор) ---
    if head in ("winner", "next"):
        if not is_admin(uid):
            await event.answer(notification="Только для оператора.")
            return
        try:
            draw_s, receipt_s = arg.split(":")
            draw_id, receipt_id = int(draw_s), int(receipt_s)
        except ValueError:
            await event.answer(notification="Некорректные данные кнопки.")
            return
        review_chat = event.message.recipient.chat_id
        if head == "winner":
            applied = services.mark_winner(draw_id, receipt_id, str(uid))
            note = texts.get("op_note_winner") if applied else texts.get("op_note_done")
        else:
            applied = services.mark_next(draw_id, receipt_id, str(uid))
            note = texts.get("op_note_next") if applied else texts.get("op_note_done")
        await event.answer(notification=note, new_text=f"🧾 Чек обработан: {note}")

        if applied and head == "winner":
            notified, num = await _notify_winner(event.bot, receipt_id)
            if not notified:
                try:
                    await event.bot.send_message(
                        chat_id=review_chat,
                        text=f"⚠️ Победителю №{num} не удалось отправить уведомление — свяжитесь вручную.",
                    )
                except Exception:  # noqa: BLE001
                    pass

        if applied:
            await send_candidate_or_summary(event.bot, review_chat, draw_id)
        return

    await event.answer(notification="Неизвестная команда.")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def build_bot() -> Bot:
    if not MAX_BOT_TOKEN:
        raise RuntimeError("MAX_BOT_TOKEN не задан в .env")
    return Bot(token=MAX_BOT_TOKEN)


async def setup_commands(bot: Bot) -> None:
    """Регистрирует список команд, который виден пользователю в интерфейсе МАКС."""
    try:
        await bot.set_my_commands(
            BotCommand(name="start", description="Начать / главное меню"),
            BotCommand(name="menu", description="Главное меню"),
            BotCommand(name="help", description="Как участвовать"),
            BotCommand(name="myid", description="Мой ID"),
        )
        logger.info("Список команд зарегистрирован")
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось зарегистрировать команды: %s", e)


async def run_polling() -> None:
    bot = build_bot()
    logger.info("MAX bot: старт polling")
    await dp.start_polling(bot)

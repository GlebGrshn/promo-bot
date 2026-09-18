"""Редактируемые тексты бота.

Значения по умолчанию заданы здесь; владелец может переопределить любой текст
через панель — переопределения хранятся в таблице settings под ключом "text:<name>".

Каждая запись: ключ -> (категория, название для панели, текст по умолчанию).
Категория: "user" (видит участник) или "operator" (видит оператор).
"""
from __future__ import annotations

import re

from . import settings

_PREFIX = "text:"

CAT_USER = "user"
CAT_OPERATOR = "operator"

DEFAULTS: dict[str, tuple[str, str, str]] = {
    # --- Тексты участнику ---
    "menu_welcome": (
        CAT_USER, "Приветствие (меню)",
        "Здравствуйте! 👋\n\nЭто бот акции: регистрируйтесь, присылайте фото чеков "
        "и участвуйте в еженедельном розыгрыше призов.\n\nВыберите действие 👇",
    ),
    "menu_registered": (
        CAT_USER, "Меню для участника",
        "Вы участвуете в акции под номером {number}.\n\nВыберите действие 👇",
    ),
    "help": (
        CAT_USER, "Как участвовать",
        "❓ Как участвовать\n\n"
        "1. Зарегистрируйтесь — укажите ФИО, номер бонусной карты, телефон и город.\n"
        "2. Присылайте фото чеков — каждый чек участвует в розыгрыше.\n"
        "3. Каждый понедельник среди новых чеков определяются победители.\n"
        "4. Если ваш чек выиграет — бот пришлёт уведомление.",
    ),
    "my_number": (
        CAT_USER, "Мой номер",
        "🔢 Ваш номер участника: {number}\nФИО: {full_name}\nГород: {city}\n"
        "Телефон: {phone}\nЧеков отправлено: {receipts}",
    ),
    "ask_name": (
        CAT_USER, "Запрос ФИО",
        "Здравствуйте! Регистрирую вас в акции.\n\nНапишите ваше ФИО (полностью):",
    ),
    "ask_card": (CAT_USER, "Запрос карты", "Введите номер вашей бонусной карты:"),
    "ask_phone": (CAT_USER, "Запрос телефона", "Введите ваш номер телефона:"),
    "ask_city": (CAT_USER, "Запрос города", "Из какого вы города?"),
    "err_name": (CAT_USER, "Ошибка: ФИО", "Пожалуйста, введите ФИО полностью (текстом)."),
    "err_card": (CAT_USER, "Ошибка: карта", "Похоже, номер карты слишком короткий. Введите ещё раз."),
    "err_phone": (CAT_USER, "Ошибка: телефон", "Похоже, это не номер телефона. Введите ещё раз."),
    "err_city": (CAT_USER, "Ошибка: город", "Введите название города."),
    "registered": (
        CAT_USER, "После регистрации",
        "✅ Готово! Ваш порядковый номер: {number}\n\n"
        "📸 Теперь присылайте фото чеков — каждый добавлю в розыгрыш. "
        "Каждый понедельник среди новых чеков разыгрываются призы.",
    ),
    "already_registered": (
        CAT_USER, "Уже зарегистрирован",
        "Вы уже зарегистрированы под номером {number}.\n\n"
        "📸 Присылайте фото чеков — каждый добавлю в розыгрыш.",
    ),
    "receipt_accepted": (CAT_USER, "Чек принят", "🧾 Принял чеков: {count}. Можете прислать ещё."),
    "receipt_failed": (CAT_USER, "Ошибка чека", "Не удалось сохранить фото. Пришлите чек ещё раз."),
    "send_receipt_prompt": (CAT_USER, "Просьба прислать чек", "📸 Пришлите фото чека — я его приму."),
    "win_notify": (
        CAT_USER, "Уведомление победителю",
        "🎉 Поздравляем, {full_name}!\n\n"
        "Ваш чек победил в розыгрыше. Ваш номер участника: {number}.\n"
        "Скоро с вами свяжутся по поводу вручения приза.",
    ),

    # --- Тексты оператору ---
    "op_candidate": (
        CAT_OPERATOR, "Карточка чека",
        "🎯 Розыгрыш #{draw_id}\n"
        "Кандидат {position} из {pool_size}\n"
        "Победителей найдено: {winners_found}/{winners_target}\n\n"
        "Участник №{number}\n"
        "ФИО: {full_name}\n"
        "Город: {city}\n"
        "Телефон: {phone}\n"
        "Карта: {card_number}\n\n"
        "Проверьте в чеке нужный товар и сумму.",
    ),
    "op_btn_winner": (CAT_OPERATOR, "Кнопка «Победитель»", "🏆 Победитель"),
    "op_btn_next": (CAT_OPERATOR, "Кнопка «Следующий»", "➡️ Следующий"),
    "op_note_winner": (CAT_OPERATOR, "Уведомление: победитель", "🏆 Отмечен победителем"),
    "op_note_next": (CAT_OPERATOR, "Уведомление: пропущен", "➡️ Пропущен"),
    "op_note_done": (CAT_OPERATOR, "Уведомление: уже обработано", "Уже обработано"),
    "op_draw_finished": (
        CAT_OPERATOR, "Итог розыгрыша",
        "🏁 Розыгрыш #{draw_id} завершён\nПобедителей: {winners_found}/{winners_target}",
    ),
    "op_winner_line": (
        CAT_OPERATOR, "Строка победителя",
        "• №{number} {full_name}, {city}, {phone}",
    ),
    "op_empty_draw": (
        CAT_OPERATOR, "Нет новых чеков",
        "🗳 Розыгрыш #{draw_id}: новых чеков нет — разыгрывать нечего.",
    ),
}


def keys(category: str | None = None) -> list[str]:
    if category is None:
        return list(DEFAULTS.keys())
    return [k for k, v in DEFAULTS.items() if v[0] == category]


def category(key: str) -> str:
    d = DEFAULTS.get(key)
    return d[0] if d else CAT_USER


def title(key: str) -> str:
    d = DEFAULTS.get(key)
    return d[1] if d else key


def default(key: str) -> str:
    d = DEFAULTS.get(key)
    return d[2] if d else ""


def raw(key: str) -> str:
    """Текущий шаблон (переопределённый или дефолтный), без подстановки."""
    override = settings.get_setting(_PREFIX + key)
    return override if override is not None else default(key)


def is_overridden(key: str) -> bool:
    return settings.get_setting(_PREFIX + key) is not None


def placeholders(key: str) -> list[str]:
    """Подстановки, ожидаемые в тексте (по умолчанию), напр. ['number']."""
    return re.findall(r"{(\w+)}", default(key))


def _safe_format(template: str, kwargs: dict) -> str:
    try:
        return template.format(**kwargs)
    except Exception:
        out = template
        for k, v in kwargs.items():
            out = out.replace("{" + k + "}", str(v))
        return out


def get(key: str, **kwargs) -> str:
    return _safe_format(raw(key), kwargs)


def set_text(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    settings.set_setting(_PREFIX + key, value)


def reset(key: str) -> None:
    settings.delete_setting(_PREFIX + key)

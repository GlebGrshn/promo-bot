"""Бизнес-логика: регистрация, чеки, розыгрыш. Не зависит от площадки."""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from .db import session_scope
from .models import (
    DRAW_ACTIVE,
    DRAW_FINISHED,
    RECEIPT_PENDING,
    RECEIPT_REJECTED,
    RECEIPT_SKIPPED,
    RECEIPT_WINNER,
    Draw,
    Participant,
    Receipt,
)


# ---------------------------------------------------------------------------
# Простые «снимки» данных (detached), чтобы безопасно отдавать наружу
# ---------------------------------------------------------------------------
@dataclass
class ParticipantView:
    id: int
    platform: str
    number: int
    full_name: str
    card_number: str
    phone: str
    city: str
    platform_user_id: str = ""  # id пользователя на площадке (для уведомлений)

    @property
    def display_number(self) -> str:
        return f"{self.number:04d}"


@dataclass
class CandidateView:
    draw_id: int
    receipt_id: int
    platform: str
    tg_file_id: Optional[str]
    file_path: Optional[str]
    position: int          # номер кандидата в пуле (с 1)
    pool_size: int
    winners_found: int
    winners_target: int
    participant: ParticipantView


@dataclass
class DrawResultView:
    draw_id: int
    status: str
    winners_found: int
    winners_target: int
    pool_size: int
    finished: bool
    reason: str            # "target" | "exhausted" | "empty" | "active"


def _p_view(p: Participant) -> ParticipantView:
    return ParticipantView(
        id=p.id,
        platform=p.platform,
        number=p.number,
        full_name=p.full_name,
        card_number=p.card_number,
        phone=p.phone,
        city=p.city,
        platform_user_id=p.platform_user_id,
    )


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------
def get_participant(platform: str, platform_user_id: str) -> Optional[ParticipantView]:
    with session_scope() as s:
        p = s.scalar(
            select(Participant).where(
                Participant.platform == platform,
                Participant.platform_user_id == str(platform_user_id),
            )
        )
        return _p_view(p) if p else None


def register_participant(
    platform: str,
    platform_user_id: str,
    full_name: str,
    card_number: str,
    phone: str,
    city: str,
) -> ParticipantView:
    """Регистрирует участника и присваивает следующий номер по площадке.

    Если участник уже есть — возвращает его без изменения номера.
    """
    with session_scope() as s:
        existing = s.scalar(
            select(Participant).where(
                Participant.platform == platform,
                Participant.platform_user_id == str(platform_user_id),
            )
        )
        if existing:
            return _p_view(existing)

        max_number = s.scalar(
            select(func.max(Participant.number)).where(Participant.platform == platform)
        )
        next_number = (max_number or 0) + 1

        p = Participant(
            platform=platform,
            platform_user_id=str(platform_user_id),
            number=next_number,
            full_name=full_name.strip(),
            card_number=card_number.strip(),
            phone=phone.strip(),
            city=city.strip(),
        )
        s.add(p)
        s.flush()  # получить id/number
        return _p_view(p)


# ---------------------------------------------------------------------------
# Чеки
# ---------------------------------------------------------------------------
def add_receipt(
    participant_id: int,
    platform: str,
    tg_file_id: Optional[str] = None,
    file_path: Optional[str] = None,
) -> int:
    with session_scope() as s:
        r = Receipt(
            participant_id=participant_id,
            platform=platform,
            tg_file_id=tg_file_id,
            file_path=file_path,
            status=RECEIPT_PENDING,
        )
        s.add(r)
        s.flush()
        return r.id


def count_pending_receipts() -> int:
    with session_scope() as s:
        return s.scalar(
            select(func.count(Receipt.id)).where(
                Receipt.status == RECEIPT_PENDING,
                Receipt.draw_id.is_(None),
            )
        ) or 0


# ---------------------------------------------------------------------------
# Розыгрыш
# ---------------------------------------------------------------------------
def _week_start(now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    monday = now - dt.timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def has_active_draw() -> bool:
    with session_scope() as s:
        return (
            s.scalar(select(func.count(Draw.id)).where(Draw.status == DRAW_ACTIVE)) or 0
        ) > 0


def start_draw(winners_target: int) -> int:
    """Создаёт розыгрыш из всех новых (неразыгранных) чеков и перемешивает их.

    Все взятые чеки помечаются draw_id — в следующий раз они уже не попадут.
    Возвращает id созданного розыгрыша.
    """
    with session_scope() as s:
        # Закрываем «зависшие» активные розыгрыши, если такие есть
        for old in s.scalars(select(Draw).where(Draw.status == DRAW_ACTIVE)).all():
            old.status = DRAW_FINISHED

        receipts = s.scalars(
            select(Receipt).where(
                Receipt.status == RECEIPT_PENDING,
                Receipt.draw_id.is_(None),
            )
        ).all()

        ids = [r.id for r in receipts]
        random.shuffle(ids)

        draw = Draw(
            week_start=_week_start(),
            winners_target=max(1, int(winners_target)),
            winners_found=0,
            order_ids=",".join(str(i) for i in ids),
            pointer=0,
            pool_size=len(ids),
            status=DRAW_FINISHED if not ids else DRAW_ACTIVE,
        )
        s.add(draw)
        s.flush()

        # Закрепляем чеки за розыгрышем
        for r in receipts:
            r.draw_id = draw.id

        return draw.id


def _won_participant_ids(s, draw_id: int) -> set[int]:
    rows = s.scalars(
        select(Receipt.participant_id).where(
            Receipt.draw_id == draw_id,
            Receipt.status == RECEIPT_WINNER,
        )
    ).all()
    return set(rows)


def _load_current(s, draw: Draw) -> Optional[Receipt]:
    """Текущий кандидат. Пропускает (и помечает skipped) чеки участников,
    которые уже победили в этом розыгрыше — один человек не выигрывает дважды.
    Указатель pointer продвигается по мере пропуска."""
    order = draw.order_list()
    won = _won_participant_ids(s, draw.id)
    while draw.pointer < len(order):
        receipt_id = order[draw.pointer]
        r = s.scalar(
            select(Receipt)
            .options(joinedload(Receipt.participant))
            .where(Receipt.id == receipt_id)
        )
        if r is None:
            draw.pointer += 1
            continue
        if r.participant_id in won:
            if r.status == RECEIPT_PENDING:
                r.status = RECEIPT_SKIPPED
                r.reviewed_at = dt.datetime.utcnow()
            draw.pointer += 1
            continue
        return r
    return None


def current_candidate(draw_id: int) -> Optional[CandidateView]:
    """Текущий кандидат на проверку. None, если розыгрыш завершён/пуст."""
    with session_scope() as s:
        draw = s.get(Draw, draw_id)
        if not draw or draw.status != DRAW_ACTIVE:
            return None
        r = _load_current(s, draw)
        if r is None:
            return None
        return CandidateView(
            draw_id=draw.id,
            receipt_id=r.id,
            platform=r.platform,
            tg_file_id=r.tg_file_id,
            file_path=r.file_path,
            position=draw.pointer + 1,
            pool_size=draw.pool_size,
            winners_found=draw.winners_found,
            winners_target=draw.winners_target,
            participant=_p_view(r.participant),
        )


def _result(draw: Draw, reason: str) -> DrawResultView:
    return DrawResultView(
        draw_id=draw.id,
        status=draw.status,
        winners_found=draw.winners_found,
        winners_target=draw.winners_target,
        pool_size=draw.pool_size,
        finished=draw.status == DRAW_FINISHED,
        reason=reason,
    )


def draw_result(draw_id: int) -> Optional[DrawResultView]:
    with session_scope() as s:
        draw = s.get(Draw, draw_id)
        if not draw:
            return None
        if draw.pool_size == 0:
            reason = "empty"
        elif draw.winners_found >= draw.winners_target:
            reason = "target"
        elif draw.status == DRAW_FINISHED:
            reason = "exhausted"
        else:
            reason = "active"
        return _result(draw, reason)


def _finish_if_done(s, draw: Draw) -> None:
    """Завершает розыгрыш, если набрано нужное число победителей
    или больше нет подходящих кандидатов (с учётом авто-пропуска)."""
    if draw.winners_found >= draw.winners_target:
        draw.status = DRAW_FINISHED
        return
    # Сбрасываем изменения в БД, иначе поиск следующего кандидата не увидит
    # только что отмеченного победителя (autoflush отключён).
    s.flush()
    if _load_current(s, draw) is None:
        draw.status = DRAW_FINISHED


def mark_winner(draw_id: int, receipt_id: int, admin: str) -> bool:
    """Отмечает чек победителем. Возвращает True, если действие применено
    (False — если кандидат устарел / гонка кнопок)."""
    with session_scope() as s:
        draw = s.get(Draw, draw_id)
        if not draw or draw.status != DRAW_ACTIVE:
            return False
        current = _load_current(s, draw)
        if current is None or current.id != receipt_id:
            return False
        current.status = RECEIPT_WINNER
        current.reviewed_at = dt.datetime.utcnow()
        current.reviewed_by = str(admin)
        draw.winners_found += 1
        draw.pointer += 1
        _finish_if_done(s, draw)
        return True


def mark_next(draw_id: int, receipt_id: int, admin: str) -> bool:
    """Отмечает чек как не подошедший и переходит к следующему."""
    with session_scope() as s:
        draw = s.get(Draw, draw_id)
        if not draw or draw.status != DRAW_ACTIVE:
            return False
        current = _load_current(s, draw)
        if current is None or current.id != receipt_id:
            return False
        current.status = RECEIPT_REJECTED
        current.reviewed_at = dt.datetime.utcnow()
        current.reviewed_by = str(admin)
        draw.pointer += 1
        _finish_if_done(s, draw)
        return True


def count_participant_receipts(participant_id: int) -> int:
    with session_scope() as s:
        return s.scalar(
            select(func.count(Receipt.id)).where(Receipt.participant_id == participant_id)
        ) or 0


def receipt_participant(receipt_id: int) -> Optional[ParticipantView]:
    """Участник, которому принадлежит чек — для уведомления победителя."""
    with session_scope() as s:
        r = s.scalar(
            select(Receipt)
            .options(joinedload(Receipt.participant))
            .where(Receipt.id == receipt_id)
        )
        return _p_view(r.participant) if r and r.participant else None


def all_participant_user_ids(platform: str) -> list[str]:
    """id пользователей площадки — для рассылки."""
    with session_scope() as s:
        rows = s.scalars(
            select(Participant.platform_user_id).where(Participant.platform == platform)
        ).all()
        return [str(r) for r in rows]


def list_winners(draw_id: int) -> list[ParticipantView]:
    with session_scope() as s:
        rows = s.scalars(
            select(Receipt)
            .options(joinedload(Receipt.participant))
            .where(Receipt.draw_id == draw_id, Receipt.status == RECEIPT_WINNER)
        ).all()
        return [_p_view(r.participant) for r in rows]


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------
def stats() -> dict:
    with session_scope() as s:
        return {
            "participants_tg": s.scalar(
                select(func.count(Participant.id)).where(Participant.platform == "tg")
            ) or 0,
            "participants_vk": s.scalar(
                select(func.count(Participant.id)).where(Participant.platform == "vk")
            ) or 0,
            "participants_max": s.scalar(
                select(func.count(Participant.id)).where(Participant.platform == "max")
            ) or 0,
            "receipts_total": s.scalar(select(func.count(Receipt.id))) or 0,
            "receipts_pending": s.scalar(
                select(func.count(Receipt.id)).where(
                    Receipt.status == RECEIPT_PENDING, Receipt.draw_id.is_(None)
                )
            ) or 0,
        }

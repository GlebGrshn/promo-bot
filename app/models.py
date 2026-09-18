"""ORM-модели."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base

# Площадки
PLATFORM_TG = "tg"
PLATFORM_VK = "vk"
PLATFORM_MAX = "max"

# Статусы чека
RECEIPT_PENDING = "pending"    # ждёт розыгрыша
RECEIPT_WINNER = "winner"      # оператор отметил победителем
RECEIPT_REJECTED = "rejected"  # оператор нажал «Следующий» (не подошёл)
RECEIPT_SKIPPED = "skipped"    # авто-пропуск: этот человек уже победил в розыгрыше

# Статусы розыгрыша
DRAW_ACTIVE = "active"
DRAW_FINISHED = "finished"


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Participant(Base):
    """Участник акции. Номер (number) отдельный для каждой площадки."""

    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_participant_user"),
        UniqueConstraint("platform", "number", name="uq_participant_number"),
    )

    id = Column(Integer, primary_key=True)
    platform = Column(String(8), nullable=False)          # tg / vk
    platform_user_id = Column(String(64), nullable=False)  # id пользователя на площадке
    number = Column(Integer, nullable=False)               # порядковый номер в рамках площадки

    full_name = Column(String(255), nullable=False)
    card_number = Column(String(64), nullable=False)
    phone = Column(String(32), nullable=False)
    city = Column(String(128), nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)

    receipts = relationship("Receipt", back_populates="participant")

    @property
    def display_number(self) -> str:
        return f"{self.number:04d}"


class Receipt(Base):
    """Чек, загруженный участником."""

    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True)
    participant_id = Column(ForeignKey("participants.id"), nullable=False)
    platform = Column(String(8), nullable=False)

    tg_file_id = Column(String(256), nullable=True)  # для чеков из Telegram
    file_path = Column(String(512), nullable=True)   # локальный путь (для чеков из ВК)

    status = Column(String(16), default=RECEIPT_PENDING, nullable=False)
    draw_id = Column(ForeignKey("draws.id"), nullable=True)  # в каком розыгрыше участвовал

    created_at = Column(DateTime, default=utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(64), nullable=True)

    participant = relationship("Participant", back_populates="receipts")
    draw = relationship("Draw", back_populates="receipts")


class Draw(Base):
    """Розыгрыш (обычно один в неделю)."""

    __tablename__ = "draws"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    week_start = Column(DateTime, nullable=True)

    winners_target = Column(Integer, nullable=False)
    winners_found = Column(Integer, default=0, nullable=False)

    order_ids = Column(Text, default="", nullable=False)  # перемешанные id чеков через запятую
    pointer = Column(Integer, default=0, nullable=False)   # указатель на текущего кандидата
    pool_size = Column(Integer, default=0, nullable=False)

    status = Column(String(16), default=DRAW_ACTIVE, nullable=False)

    receipts = relationship("Receipt", back_populates="draw")

    def order_list(self) -> list[int]:
        if not self.order_ids:
            return []
        return [int(x) for x in self.order_ids.split(",") if x]


class Setting(Base):
    """Простое key-value хранилище настроек (например, число победителей)."""

    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)

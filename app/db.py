"""Настройка базы данных (SQLite + SQLAlchemy, синхронный режим)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,  # объекты остаются пригодными после закрытия сессии
    future=True,
)

Base = declarative_base()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Контекст с автоматическим commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (регистрация моделей)

    Base.metadata.create_all(engine)

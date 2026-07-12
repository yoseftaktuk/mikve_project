import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .settings import settings


class Base(DeclarativeBase):
    pass


class AccessLog(Base):
    __tablename__ = "access_logs"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    uid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # granted|denied
    reason: Mapped[str] = mapped_column(String(60), nullable=False)  # insufficient_balance|disabled|unknown_chip|...
    fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    balance_before_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_after_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HardwareEvent(Base):
    __tablename__ = "hardware_events"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


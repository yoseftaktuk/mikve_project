import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .settings import settings


class Base(DeclarativeBase):
    pass


class Chip(Base):
    __tablename__ = "chips"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # RFID/NFC UID
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = {"schema": settings.postgres_schema}

    chip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{settings.postgres_schema}.chips.id"), primary_key=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChipActivity(Base):
    __tablename__ = "chip_activity"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{settings.postgres_schema}.chips.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # scan, recharge, debit, disable, enable
    delta_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


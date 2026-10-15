import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .settings import settings


class Base(DeclarativeBase):
    pass


STATUS_SUB_ACTIVE = "active"


class Member(Base):
    __tablename__ = "members"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # uid is the hardware identifier: a hex RFID/NFC UID (e.g. "A1B2C3D4")
    # or "FP-<slot>" for fingerprint sensor templates (e.g. "FP-12").
    # Both entry methods share this single table (members) so member_id
    # is the generic FK used across all services regardless of entry method.
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    holder_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(9), unique=True, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = {"schema": settings.postgres_schema}

    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{settings.postgres_schema}.members.id"), primary_key=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MemberActivity(Base):
    __tablename__ = "member_activity"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{settings.postgres_schema}.members.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # scan, recharge, debit, disable, enable
    delta_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional caller-supplied key. Unique so a retried adjust never double-applies.
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonthlySubscription(Base):
    """One active Hebrew-month subscription per member (free entrances per Israel day)."""

    __tablename__ = "monthly_subscriptions"
    __table_args__ = (
        UniqueConstraint("member_id", "hebrew_year", "hebrew_month", name="uq_monthly_sub_member_month"),
        {"schema": settings.postgres_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.postgres_schema}.members.id"), index=True, nullable=False
    )
    hebrew_year: Mapped[int] = mapped_column(Integer, nullable=False)
    hebrew_month: Mapped[int] = mapped_column(Integer, nullable=False)
    hebrew_month_name: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nedarim_transaction_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_SUB_ACTIVE)
    last_free_entry_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

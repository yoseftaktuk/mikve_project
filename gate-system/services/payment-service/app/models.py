import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .settings import settings


class Base(DeclarativeBase):
    pass


CURRENCY_ILS = 1

# A top-up is claimed exactly once: only a row still in PENDING may move to
# CREDITING, and only the mover credits the balance. A row left in CREDITING
# means the process died mid-credit and needs a human.
STATUS_PENDING = "pending"
STATUS_CREDITING = "crediting"
STATUS_PAID = "paid"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"

PRODUCT_BALANCE = "balance"
PRODUCT_MONTHLY_SUBSCRIPTION = "monthly_subscription"

WEBHOOK_RECEIVED = "received"
WEBHOOK_IGNORED_CATEGORY = "ignored_category"
WEBHOOK_USER_UNRESOLVED = "user_unresolved"
WEBHOOK_INVALID = "invalid"
WEBHOOK_PROCESSED = "processed"
WEBHOOK_FAILED = "failed"
WEBHOOK_DUPLICATE = "duplicate"


class CardTopup(Base):
    __tablename__ = "card_topups"
    __table_args__ = {"schema": settings.postgres_schema}

    # Also the correlation id: it is unguessable, so it doubles as the secret
    # inside the callback URL handed to Nedarim.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    fingerprint_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=CURRENCY_ILS)
    product: Mapped[str] = mapped_column(String(32), nullable=False, default=PRODUCT_BALANCE)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING, index=True)

    # Id handed back by CreateTransaction, which the kiosk gives to the iframe.
    nedarim_created_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Id reported by the callback. Unique, so the same cleared transaction can
    # never be credited through two different top-up rows.
    nedarim_transaction_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    confirmation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_num: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # Sent to Nedarim so a retried create cannot open two transactions.
    ajax_id: Mapped[str] = mapped_column(String(32), nullable=False)

    balance_after_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NedarimCallback(Base):
    """Every inbound callback, accepted or not.

    Nedarim sends each update once and never retries, so a rejected or
    malformed delivery is only ever visible here.
    """

    __tablename__ = "nedarim_callbacks"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topup_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NedarimWebhookEvent(Base):
    """Institution-level Nedarim webhook (donations not initiated by this app)."""

    __tablename__ = "nedarim_webhook_events"
    __table_args__ = {"schema": settings.postgres_schema}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    zeout: Mapped[str | None] = mapped_column(String(9), index=True, nullable=True)
    groupe: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    transaction_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_chip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WEBHOOK_RECEIVED, index=True
    )
    processing_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

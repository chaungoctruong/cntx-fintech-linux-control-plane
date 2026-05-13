from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    tokens: Mapped[list["Token"]] = relationship(back_populates="partner")
    grants: Mapped[list["PartnerBotGrant"]] = relationship(back_populates="partner")


class PartnerBotGrant(Base):
    __tablename__ = "partner_bot_grants"
    __table_args__ = (UniqueConstraint("partner_id", "bot_id", name="uq_partner_bot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[str] = mapped_column(
        ForeignKey("partners.id"), nullable=False, index=True
    )
    bot_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String(255))

    partner: Mapped[Partner] = relationship(back_populates="grants")


class Token(Base):
    __tablename__ = "tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    bot_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    end_user_telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    end_user_username: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    expiry_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    renewed_to_jti: Mapped[str | None] = mapped_column(String(64), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    force_stop_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    force_stop_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    force_stop_last_attempt: Mapped[datetime | None] = mapped_column(DateTime)
    force_stop_last_error: Mapped[str | None] = mapped_column(String(255))

    partner: Mapped[Partner] = relationship(back_populates="tokens")

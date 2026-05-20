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
    billing_anchor_at: Mapped[datetime | None] = mapped_column(DateTime)

    tokens: Mapped[list["Token"]] = relationship(back_populates="partner")
    grants: Mapped[list["PartnerBotGrant"]] = relationship(back_populates="partner")
    members: Mapped[list["PartnerMember"]] = relationship(back_populates="partner")
    payment_proofs: Mapped[list["PartnerPaymentProof"]] = relationship(back_populates="partner")
    billing_notices: Mapped[list["PartnerBillingNotice"]] = relationship(back_populates="partner")
    billing_snapshots: Mapped[list["PartnerBillingSnapshot"]] = relationship(back_populates="partner")


class PartnerMember(Base):
    __tablename__ = "partner_members"
    __table_args__ = (
        UniqueConstraint("partner_id", "telegram_user_id", name="uq_partner_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(24), default="operator", nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    added_by_admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    partner: Mapped[Partner] = relationship(back_populates="members")


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
    issued_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    issued_by_username: Mapped[str | None] = mapped_column(String(64))
    expiry_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    renewed_to_jti: Mapped[str | None] = mapped_column(String(64), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    force_stop_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    force_stop_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    force_stop_last_attempt: Mapped[datetime | None] = mapped_column(DateTime)
    force_stop_last_error: Mapped[str | None] = mapped_column(String(255))

    partner: Mapped[Partner] = relationship(back_populates="tokens")


class PartnerBillingNotice(Base):
    __tablename__ = "partner_billing_notices"
    __table_args__ = (
        UniqueConstraint("partner_id", "billing_month", "week_key", name="uq_partner_billing_notice_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    billing_month: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    week_key: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    billable_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    support_active_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_due_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    partner: Mapped[Partner] = relationship(back_populates="billing_notices")


class PartnerPaymentProof(Base):
    __tablename__ = "partner_payment_proofs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    billing_month: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    week_key: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amount_due_snapshot_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_confirmed_usd: Mapped[int | None] = mapped_column(Integer)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    submitted_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="submitted", nullable=False, index=True)
    confirmed_by_admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejected_by_admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    partner: Mapped[Partner] = relationship(back_populates="payment_proofs")


class PartnerBillingSnapshot(Base):
    __tablename__ = "partner_billing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    payment_proof_id: Mapped[int | None] = mapped_column(ForeignKey("partner_payment_proofs.id"), index=True)
    billing_period_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    week_key: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    period_start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cycle_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    billable_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    support_active_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    block_size: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    blocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    user_fee_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    support_fee_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    infra_fee_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_fee_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmed_paid_before_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmed_amount_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_due_after_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_details_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger)

    partner: Mapped[Partner] = relationship(back_populates="billing_snapshots")
    payment_proof: Mapped[PartnerPaymentProof | None] = relationship()

"""The 7 tables (see docs/data-model.md — keep the two in sync in the same PR).

v0 columns are a starting point; evolve via alembic, never by hand-editing prod.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # Fernet (auth/crypto)
    gmail_history_id: Mapped[str | None] = mapped_column(String(32))  # incremental sync cursor
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Thread(TimestampMixin, Base):
    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("user_id", "gmail_thread_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    gmail_thread_id: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str | None] = mapped_column(Text)
    last_msg_id: Mapped[str | None] = mapped_column(String(32))  # half of the summary cache key
    last_msg_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    needs_reply: Mapped[bool | None] = mapped_column(Boolean)  # classifier output, set at ingest


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("user_id", "gmail_msg_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    gmail_msg_id: Mapped[str] = mapped_column(String(32))
    from_addr: Mapped[str | None] = mapped_column(String(320))
    to_addrs: Mapped[str | None] = mapped_column(Text)  # comma-joined; normalise later if needed
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_from_user: Mapped[bool] = mapped_column(Boolean, default=False)  # feeds RAG (sent mail)
    body_clean: Mapped[str | None] = mapped_column(Text)  # cleaned; raw is NEVER stored


class Summary(TimestampMixin, Base):
    __tablename__ = "summaries"
    # THE cache key: same thread + same last message => cache hit, no model call
    __table_args__ = (UniqueConstraint("thread_id", "last_msg_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    last_msg_id: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(80))  # ablation bookkeeping


class Entity(TimestampMixin, Base):
    __tablename__ = "entities"
    # Upsert target: one row per (user, type, key) — the architecture doc's UNIQUE rule
    __table_args__ = (UniqueConstraint("user_id", "type", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))  # flight | tracking_number | amount | ...
    key: Mapped[str] = mapped_column(String(200))  # e.g. "QF430 2026-09-02"
    value: Mapped[str] = mapped_column(Text)
    source_msg_id: Mapped[str | None] = mapped_column(String(32))  # provenance
    extraction_tier: Mapped[str | None] = mapped_column(String(10))  # regex | llm


class Commitment(TimestampMixin, Base):
    __tablename__ = "commitments"
    __table_args__ = (UniqueConstraint("user_id", "source_msg_id", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(16))  # user_owes | owed_to_user
    body: Mapped[str] = mapped_column(Text)  # "send the report to Priya"
    fingerprint: Mapped[str] = mapped_column(String(64))  # hash for dedupe
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | lapsed
    source_msg_id: Mapped[str | None] = mapped_column(String(32))


class Draft(TimestampMixin, Base):
    __tablename__ = "drafts"
    __table_args__ = (
        # FTS over drafts ("what did I already say about X") — GIN, english config
        Index("ix_drafts_body_fts", text("to_tsvector('english', body)"), postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    # draft | approved | sent | discarded
    status: Mapped[str] = mapped_column(String(12), default="draft")
    model_used: Mapped[str | None] = mapped_column(String(80))
    sent_gmail_msg_id: Mapped[str | None] = mapped_column(String(32))

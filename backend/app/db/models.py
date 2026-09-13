"""Mailbox and assistant tables (keep docs/data-model.md in sync in the same PR).

v0 columns are a starting point; evolve via alembic, never by hand-editing prod.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # Fernet; short-lived
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gmail_history_id: Mapped[str | None] = mapped_column(String(32))  # incremental sync cursor
    sync_version: Mapped[int] = mapped_column(server_default="0")
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
    version: Mapped[int] = mapped_column(server_default="0")


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
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str | None] = mapped_column(Text)
    reply_metadata: Mapped[dict | None] = mapped_column(JSONB)
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


class ContextSnapshot(TimestampMixin, Base):
    __tablename__ = "context_snapshots"
    __table_args__ = (UniqueConstraint("id", "user_id", name="uq_context_owner"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    source_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)


class AssistantTask(TimestampMixin, Base):
    __tablename__ = "assistant_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_task_request"),
        Index("ix_tasks_history", "user_id", "created_at", "id"),
        UniqueConstraint("id", "user_id", name="uq_task_owner"),
        ForeignKeyConstraint(
            ["context_snapshot_id", "user_id"],
            ["context_snapshots.id", "context_snapshots.user_id"],
            ondelete="CASCADE",
            name="fk_task_owned_context",
        ),
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled',"
            "'needs_clarification','unsupported')",
            name="ck_task_state",
        ),
        CheckConstraint("version >= 1 AND latest_sequence >= 1", name="ck_task_versions"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    instruction: Mapped[str] = mapped_column(Text)
    context_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    intent_hint: Mapped[str | None] = mapped_column(String(16))
    route: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(default=1)
    latest_sequence: Mapped[int] = mapped_column(default=1)
    release: Mapped[dict] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))


class AssistantJob(TimestampMixin, Base):
    __tablename__ = "assistant_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_job_owned_task",
        ),
        CheckConstraint("state IN ('queued','running','done')", name="ck_job_state"),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_job_attempts"),
        CheckConstraint(
            "(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state != 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_job_lease",
        ),
        Index("ix_jobs_claim", "state", "available_at", "lease_expires_at"),
    )

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_event_owned_task",
        ),
        CheckConstraint("sequence >= 1 AND task_version >= 1", name="ck_event_versions"),
    )

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column()
    task_version: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactRevision(TimestampMixin, Base):
    __tablename__ = "artifact_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["assistant_tasks.id", "assistant_tasks.user_id"],
            ondelete="CASCADE",
            name="fk_artifact_owned_task",
        ),
        UniqueConstraint("task_id", name="uq_summary_task_artifact"),
        CheckConstraint("revision = 1", name="ck_artifact_initial_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[int] = mapped_column(index=True)
    revision: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)

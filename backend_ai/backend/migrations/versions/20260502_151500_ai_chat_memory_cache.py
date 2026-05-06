"""add persistent AI chat memory and answer cache

Revision ID: 20260502_151500
Revises: 20260502_135632
Create Date: 2026-05-02 15:15:00 +07

Adds PostgreSQL-backed chat history and user-scoped exact answer cache for the
Linux control-plane AI chat endpoint. The migration is additive only.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_151500"
down_revision: Union[str, None] = "20260502_135632"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_chat_messages (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'chat',
            status TEXT NOT NULL DEFAULT 'done',
            source TEXT NOT NULL DEFAULT 'executor',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_user_created
        ON ai_chat_messages(user_id, created_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_hash
        ON ai_chat_messages(content_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_mode_created
        ON ai_chat_messages(mode, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_chat_answer_cache (
            id BIGSERIAL PRIMARY KEY,
            scope TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'chat',
            question_hash TEXT NOT NULL,
            normalized_question TEXT NOT NULL,
            sample_question TEXT NOT NULL,
            answer TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'executor',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            hit_count BIGINT NOT NULL DEFAULT 0,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            last_hit_at BIGINT NULL,
            UNIQUE(scope, mode, question_hash)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_scope_updated
        ON ai_chat_answer_cache(scope, mode, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_hash
        ON ai_chat_answer_cache(question_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_enabled_updated
        ON ai_chat_answer_cache(enabled, updated_at DESC)
        """
    )


def downgrade() -> None:
    raise RuntimeError("AI chat memory cache downgrade is unsupported; restore from backup/PITR instead.")

"""add platform AI knowledge store

Revision ID: 20260502_153000
Revises: 20260502_151500
Create Date: 2026-05-02 15:30:00 +07

Adds an additive PostgreSQL knowledge store for curated platform/internet
content used by the Linux control-plane AI RAG layer.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_153000"
down_revision: Union[str, None] = "20260502_151500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_platform_knowledge_sources (
            id BIGSERIAL PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'manual',
            title TEXT NOT NULL,
            url TEXT NULL,
            trust_level INTEGER NOT NULL DEFAULT 50 CHECK(trust_level >= 0 AND trust_level <= 100),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            last_ingested_at BIGINT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_sources_enabled
        ON ai_platform_knowledge_sources(enabled, trust_level DESC, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_sources_type
        ON ai_platform_knowledge_sources(source_type, enabled)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_platform_knowledge_chunks (
            id BIGSERIAL PRIMARY KEY,
            source_key TEXT NOT NULL REFERENCES ai_platform_knowledge_sources(source_key) ON DELETE CASCADE,
            content_hash TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NULL,
            content TEXT NOT NULL,
            normalized_content TEXT NOT NULL,
            trust_level INTEGER NOT NULL DEFAULT 50 CHECK(trust_level >= 0 AND trust_level <= 100),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            UNIQUE(source_key, content_hash)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_enabled
        ON ai_platform_knowledge_chunks(enabled, trust_level DESC, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_source
        ON ai_platform_knowledge_chunks(source_key, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_hash
        ON ai_platform_knowledge_chunks(content_hash)
        """
    )


def downgrade() -> None:
    raise RuntimeError("AI platform knowledge downgrade is unsupported; restore from backup/PITR instead.")

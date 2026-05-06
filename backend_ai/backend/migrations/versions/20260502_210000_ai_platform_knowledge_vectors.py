"""add optional semantic vectors for AI platform knowledge

Revision ID: 20260502_210000
Revises: 20260502_161500
Create Date: 2026-05-02 21:00:00 +07

Adds additive embedding metadata to the PostgreSQL knowledge store. pgvector is
used only when the database extension is available; otherwise the JSONB
embedding fallback keeps the migration non-fatal for managed Postgres plans that
do not expose the extension yet.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_210000"
down_revision: Union[str, None] = "20260502_161500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_platform_knowledge_chunks
            ADD COLUMN IF NOT EXISTS embedding_json JSONB NULL,
            ADD COLUMN IF NOT EXISTS embedding_model TEXT NULL,
            ADD COLUMN IF NOT EXISTS embedding_dim INTEGER NULL,
            ADD COLUMN IF NOT EXISTS embedding_updated_at BIGINT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_embedding_model
        ON ai_platform_knowledge_chunks(embedding_model, embedding_updated_at DESC)
        WHERE embedding_json IS NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'pgvector extension unavailable, semantic retrieval will use JSON fallback only: %', SQLERRM;
            END;

            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                EXECUTE 'ALTER TABLE ai_platform_knowledge_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024)';
                EXECUTE 'CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_pgvector_model ON ai_platform_knowledge_chunks(embedding_model, embedding_updated_at DESC) WHERE embedding IS NOT NULL';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("AI platform knowledge vector downgrade is unsupported; restore from backup/PITR instead.")

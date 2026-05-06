"""add AI training data pipeline tables

Revision ID: 20260502_160000
Revises: 20260502_153000
Create Date: 2026-05-02 16:00:00 +07

Adds additive PostgreSQL tables for product-grade AI training data curation,
dataset export tracking, and model version registry. This migration does not
train or deploy a model.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_160000"
down_revision: Union[str, None] = "20260502_153000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_training_examples (
            id BIGSERIAL PRIMARY KEY,
            example_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'chat',
            source_ref TEXT NULL,
            user_id TEXT NULL,
            scope TEXT NOT NULL DEFAULT 'platform',
            mode TEXT NOT NULL DEFAULT 'chat',
            prompt TEXT NOT NULL,
            completion TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            completion_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'approved', 'rejected', 'exported', 'trained')),
            quality_score NUMERIC(5,4) NOT NULL DEFAULT 0,
            safety_status TEXT NOT NULL DEFAULT 'safe'
                CHECK(safety_status IN ('safe', 'redacted', 'blocked')),
            skip_reason TEXT NULL,
            redaction_count INTEGER NOT NULL DEFAULT 0,
            reviewer_id TEXT NULL,
            reviewed_at BIGINT NULL,
            exported_at BIGINT NULL,
            trained_at BIGINT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_training_examples_status_updated
        ON ai_training_examples(status, updated_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_training_examples_mode_status
        ON ai_training_examples(mode, status, quality_score DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_training_examples_prompt_hash
        ON ai_training_examples(prompt_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_training_examples_source_created
        ON ai_training_examples(source, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_training_exports (
            id BIGSERIAL PRIMARY KEY,
            export_key TEXT NOT NULL UNIQUE,
            format TEXT NOT NULL DEFAULT 'jsonl',
            output_path TEXT NOT NULL,
            checksum TEXT NOT NULL,
            example_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'created',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_training_exports_created
        ON ai_training_exports(created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_model_versions (
            id BIGSERIAL PRIMARY KEY,
            model_key TEXT NOT NULL UNIQUE,
            base_model TEXT NOT NULL,
            adapter_path TEXT NULL,
            dataset_export_key TEXT NULL,
            status TEXT NOT NULL DEFAULT 'candidate'
                CHECK(status IN ('candidate', 'staging', 'active', 'retired', 'failed')),
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            activated_at BIGINT NULL,
            retired_at BIGINT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_model_versions_status_created
        ON ai_model_versions(status, created_at DESC)
        """
    )


def downgrade() -> None:
    raise RuntimeError("AI training pipeline downgrade is unsupported; restore from backup/PITR instead.")

"""add AI model evaluation registry

Revision ID: 20260502_161500
Revises: 20260502_160000
Create Date: 2026-05-02 16:15:00 +07

Adds an additive registry for dataset/model evaluation runs. This supports
versioned LoRA/fine-tune promotion without deploying models by intuition.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_161500"
down_revision: Union[str, None] = "20260502_160000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_model_eval_runs (
            id BIGSERIAL PRIMARY KEY,
            run_key TEXT NOT NULL UNIQUE,
            model_key TEXT NOT NULL,
            dataset_export_key TEXT NULL,
            eval_type TEXT NOT NULL DEFAULT 'dataset_static',
            status TEXT NOT NULL DEFAULT 'created'
                CHECK(status IN ('created', 'running', 'completed', 'failed')),
            example_count INTEGER NOT NULL DEFAULT 0,
            score NUMERIC(6,5) NOT NULL DEFAULT 0,
            pass_threshold NUMERIC(6,5) NOT NULL DEFAULT 0.8,
            passed BOOLEAN NOT NULL DEFAULT FALSE,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at BIGINT NOT NULL,
            completed_at BIGINT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_model_created
        ON ai_model_eval_runs(model_key, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_dataset_created
        ON ai_model_eval_runs(dataset_export_key, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_status_created
        ON ai_model_eval_runs(status, created_at DESC)
        """
    )


def downgrade() -> None:
    raise RuntimeError("AI model eval registry downgrade is unsupported; restore from backup/PITR instead.")

"""add GsAlgo runner bot state records

Revision ID: 20260503_170000
Revises: 20260502_210000
Create Date: 2026-05-03 17:00:00 +07

Adds the PostgreSQL system-of-record table used by the runner-only
`/api/v2/runner/bot-state/gsalgo` endpoint. The table stores control-plane
state records and idempotency keys only; credentials and broker execution are
out of scope.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260503_170000"
down_revision: Union[str, None] = "20260502_210000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runner_bot_state_records (
            id BIGSERIAL PRIMARY KEY,
            bot_id TEXT NOT NULL,
            schema_name TEXT NOT NULL DEFAULT 'gsalgo_backend_state.v1',
            operation TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_key TEXT NOT NULL,
            account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            deployment_id BIGINT NOT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
            runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
            slot_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'recorded',
            symbol TEXT NULL,
            side TEXT NULL,
            realized_pnl NUMERIC NULL,
            occurred_at TIMESTAMPTZ NULL,
            closed_at TIMESTAMPTZ NULL,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_bot_state_record
        ON runner_bot_state_records(bot_id, record_type, account_id, deployment_id, record_key)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_bot_state_context
        ON runner_bot_state_records(account_id, deployment_id, bot_id, record_type, status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_bot_state_runner
        ON runner_bot_state_records(runner_id, slot_id, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runner_bot_state_pnl
        ON runner_bot_state_records(account_id, deployment_id, bot_id, record_type, occurred_at DESC)
        WHERE realized_pnl IS NOT NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError("GsAlgo bot state downgrade is unsupported; restore from backup/PITR instead.")

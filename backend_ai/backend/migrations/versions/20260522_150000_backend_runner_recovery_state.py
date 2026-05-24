"""add backend runner recovery state

Revision ID: 20260522_150000
Revises: 20260511_120000
Create Date: 2026-05-22 15:00:00 +07

Tracks Linux-control-plane recovery decisions when Windows runners defer MT5
worker/terminal recovery to the backend.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260522_150000"
down_revision: Union[str, None] = "20260511_120000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE bot_deployments
            ADD COLUMN IF NOT EXISTS last_runner_recovery_reason TEXT NULL,
            ADD COLUMN IF NOT EXISTS last_runner_recovery_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_first_seen_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_last_seen_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_attempt_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS runner_recovery_window_started_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_cooldown_until TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_in_flight BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS runner_recovery_in_flight_since TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_last_command_id TEXT NULL,
            ADD COLUMN IF NOT EXISTS runner_recovery_last_command_at TIMESTAMPTZ NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE bot_deployments
            DROP COLUMN IF EXISTS runner_recovery_last_command_at,
            DROP COLUMN IF EXISTS runner_recovery_last_command_id,
            DROP COLUMN IF EXISTS runner_recovery_in_flight_since,
            DROP COLUMN IF EXISTS runner_recovery_in_flight,
            DROP COLUMN IF EXISTS runner_recovery_cooldown_until,
            DROP COLUMN IF EXISTS runner_recovery_window_started_at,
            DROP COLUMN IF EXISTS runner_recovery_attempt_count,
            DROP COLUMN IF EXISTS runner_recovery_last_seen_at,
            DROP COLUMN IF EXISTS runner_recovery_first_seen_at,
            DROP COLUMN IF EXISTS last_runner_recovery_at,
            DROP COLUMN IF EXISTS last_runner_recovery_reason
        """
    )

"""add intent_seq to bot_deployments

Revision ID: 20260511_120000
Revises: 20260503_170000
Create Date: 2026-05-11 12:00:00 +07

Stores a monotonically-increasing intent counter per deployment that is bumped
every time the user expresses a START/STOP intent for the underlying account.
The control plane attaches the captured seq onto every START_BOT/STOP_BOT and
re-reads the row at dispatch time so stale intents (eg. a replacement START
queued before the user pressed OFF) never reach the runner.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260511_120000"
down_revision: Union[str, None] = "20260503_170000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE bot_deployments "
        "ADD COLUMN IF NOT EXISTS intent_seq INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE bot_deployments DROP COLUMN IF EXISTS intent_seq")

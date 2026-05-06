"""baseline current control-plane schema

Revision ID: 20260502_135632
Revises:
Create Date: 2026-05-02 13:56:32 +07

This revision is a baseline marker for the schema currently created by
`init_pg_schema.py`. It is intentionally non-destructive and should be stamped
onto existing databases after the bootstrap verifier has confirmed the current
schema.

During the transition, empty scratch databases should still be prepared through
`init_pg_schema.py` before stamping this baseline. Future schema changes must
be expressed as new Alembic revisions instead of only changing runtime
bootstrap DDL.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260502_135632"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline marker only. Existing databases should use `alembic stamp head`.
    op.execute("SELECT 1")


def downgrade() -> None:
    raise RuntimeError("Baseline downgrade is unsupported; restore from backup/PITR instead.")

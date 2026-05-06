from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SQL_ROOT = Path(__file__).resolve().parent / "sql"


@lru_cache(maxsize=256)
def load_sql(relative_path: str) -> str:
    """Load a SQL template from control_plane/sql with simple caching."""
    normalized = str(relative_path or "").strip().lstrip("/")
    if not normalized:
        raise ValueError("relative_path_required")
    target = (_SQL_ROOT / normalized).resolve()
    if not str(target).startswith(str(_SQL_ROOT.resolve())):
        raise ValueError("invalid_sql_path")
    return target.read_text(encoding="utf-8")

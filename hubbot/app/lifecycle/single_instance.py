# -*- coding: utf-8 -*-
"""Single-instance lock (flock) so only one hubbot polling runs per token."""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Optional

from app.debug import _dbg_lock

log = logging.getLogger("hubbot")

_INSTANCE_LOCK_FILE: Optional[Any] = None


def acquire_single_instance_lock(token_fingerprint: str) -> tuple[bool, str]:
    global _INSTANCE_LOCK_FILE
    lock_name = f".hubbot.{token_fingerprint}.lock"
    # Resolve relative to hubbot/ (parent of app/)
    lock_path = Path(__file__).resolve().parents[2] / lock_name

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _dbg_lock(
            "lock acquire start",
            {
                "pid": os.getpid(),
                "lock_path": str(lock_path),
                "lock_exists": lock_path.exists(),
                "token_fingerprint": token_fingerprint,
            },
            hypothesis_id="H1_H2_H3_H4",
            run_id="pre-fix",
        )

        if lock_path.exists():
            try:
                with open(lock_path, "r", encoding="utf-8") as rf:
                    content = rf.read().strip()
                    _dbg_lock(
                        "existing lock file content read",
                        {"pid": os.getpid(), "content_preview": content[:24], "content_isdigit": content.isdigit()},
                        hypothesis_id="H1_H2_H3",
                        run_id="pre-fix",
                    )
                    if content.isdigit():
                        old_pid = int(content)
                        try:
                            os.kill(old_pid, 0)
                            _dbg_lock(
                                "existing pid is alive",
                                {"pid": os.getpid(), "old_pid": old_pid},
                                hypothesis_id="H2",
                                run_id="pre-fix",
                            )
                        except OSError:
                            log.warning("Stale lock detected. Cleaning old PID %s.", old_pid)
                            lock_path.unlink(missing_ok=True)
                            _dbg_lock(
                                "stale lock removed by dead pid check",
                                {"pid": os.getpid(), "old_pid": old_pid, "lock_exists_after_unlink": lock_path.exists()},
                                hypothesis_id="H1",
                                run_id="pre-fix",
                            )
                    else:
                        log.warning("Lock file corrupted. Removing.")
                        lock_path.unlink(missing_ok=True)
                        _dbg_lock(
                            "corrupted lock content removed",
                            {"pid": os.getpid(), "lock_exists_after_unlink": lock_path.exists()},
                            hypothesis_id="H3",
                            run_id="pre-fix",
                        )
            except (OSError, ValueError):
                log.warning("Failed reading lock file. Removing stale lock.")
                lock_path.unlink(missing_ok=True)
                _dbg_lock(
                    "lock read error path removed stale lock",
                    {"pid": os.getpid(), "lock_exists_after_unlink": lock_path.exists()},
                    hypothesis_id="H1_H3_H4",
                    run_id="pre-fix",
                )

        f = open(lock_path, "a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.seek(0)
            f.truncate(0)
            f.write(str(os.getpid()))
            f.flush()
            _INSTANCE_LOCK_FILE = f
            _dbg_lock(
                "flock acquired and pid written",
                {"pid": os.getpid(), "lock_path": str(lock_path)},
                hypothesis_id="H3_H4",
                run_id="pre-fix",
            )
            return True, str(lock_path)
        except (BlockingIOError, OSError):
            try:
                f.close()
            except Exception:
                pass
            _dbg_lock(
                "flock blocked by another holder",
                {"pid": os.getpid(), "lock_path": str(lock_path)},
                hypothesis_id="H2_H4",
                run_id="pre-fix",
            )
            return False, str(lock_path)
    except Exception as e:
        log.error("Lock error: %s", e)
        return True, str(lock_path)


def release_single_instance_lock() -> None:
    global _INSTANCE_LOCK_FILE
    if _INSTANCE_LOCK_FILE is None:
        return
    try:
        import fcntl
        fcntl.flock(_INSTANCE_LOCK_FILE.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _INSTANCE_LOCK_FILE.close()
    except Exception:
        pass
    _INSTANCE_LOCK_FILE = None

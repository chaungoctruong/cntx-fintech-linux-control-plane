from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import Request

from app.settings import settings
from app.store import Store

_PROCESS_STORE: Optional[Store] = None
_PROCESS_STORE_LOCK = Lock()


def make_store() -> Store:
    storage_root = Path(settings.DATA_DIR).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    store = Store(storage_root)
    store.init()
    return store


def get_process_store() -> Store:
    global _PROCESS_STORE
    if _PROCESS_STORE is not None:
        return _PROCESS_STORE
    with _PROCESS_STORE_LOCK:
        if _PROCESS_STORE is None:
            _PROCESS_STORE = make_store()
        return _PROCESS_STORE


def set_process_store(store: Optional[Store]) -> None:
    global _PROCESS_STORE
    with _PROCESS_STORE_LOCK:
        _PROCESS_STORE = store


def close_process_store() -> None:
    global _PROCESS_STORE
    with _PROCESS_STORE_LOCK:
        store = _PROCESS_STORE
        _PROCESS_STORE = None
    if store is not None:
        close = getattr(store, "close", None) or getattr(store, "closeall", None)
        if callable(close):
            close()


def get_store(request: Request) -> Store:
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store
    store = get_process_store()
    request.app.state.store = store
    return store

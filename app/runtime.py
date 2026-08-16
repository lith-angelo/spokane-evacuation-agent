"""Small process-local runtime controls that are safe to change from the UI."""

from __future__ import annotations

from threading import RLock

from app.config import settings

_VALID_MODES = frozenset({"live", "replay"})
_lock = RLock()
_data_mode = settings.data_mode.strip().lower()
if _data_mode not in _VALID_MODES:
    _data_mode = "replay"


def data_mode() -> str:
    with _lock:
        return _data_mode


def is_replay() -> bool:
    return data_mode() == "replay"


def set_data_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in _VALID_MODES:
        raise ValueError("mode must be 'live' or 'replay'")
    global _data_mode
    with _lock:
        _data_mode = normalized
    return normalized

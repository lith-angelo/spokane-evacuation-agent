from __future__ import annotations

import pytest

from app import runtime
from app.session import SessionRegistry


def test_runtime_mode_can_switch_both_directions():
    original = runtime.data_mode()
    try:
        assert runtime.set_data_mode("live") == "live"
        assert runtime.is_replay() is False
        assert runtime.set_data_mode("replay") == "replay"
        assert runtime.is_replay() is True
    finally:
        runtime.set_data_mode(original)


def test_runtime_mode_rejects_unknown_values():
    with pytest.raises(ValueError):
        runtime.set_data_mode("hybrid")


def test_registry_clear_returns_number_of_discarded_sessions():
    registry = SessionRegistry()
    registry.create()
    registry.create()

    assert registry.clear() == 2
    assert registry.all() == []

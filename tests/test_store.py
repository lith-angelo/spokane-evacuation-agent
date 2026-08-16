from types import SimpleNamespace

from app import store


def test_purge_all_removes_demo_session_and_snapshot_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "settings", SimpleNamespace(db_path=tmp_path / "sessions.db"))
    store.init()
    store.save_session("demo", "2026-08-15T00:00:00Z", "2026-08-15T00:00:00Z", {"x": 1})
    store.append_snapshot(
        session_id="demo",
        source_id="MAPBOX",
        url="https://example.test/[REDACTED]",
        outcome="OK",
        status=200,
        fetched_at="2026-08-15T00:00:00Z",
        body="{}",
    )

    counts = store.purge_all()

    assert counts == {"sessions": 1, "steps": 0, "snapshots": 1}
    assert store.load_session("demo") is None
    assert store.snapshot_count() == 0

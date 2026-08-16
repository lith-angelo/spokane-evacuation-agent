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


def test_route_skill_lessons_are_bounded_non_household_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "settings", SimpleNamespace(db_path=tmp_path / "sessions.db"))
    store.init()

    store.record_skill_lesson("compare_alternatives", "2026-08-15T00:00:00Z")
    store.record_skill_lesson("compare_alternatives", "2026-08-16T00:00:00Z")
    store.record_skill_lesson("prefer_fire_clearance", "2026-08-16T01:00:00Z")
    store.record_skill_lesson(
        "prefer_fire_clearance", "2026-08-16T02:00:00Z", mode="live"
    )

    rows = store.load_skill_lessons(limit=1)
    assert len(rows) == 1
    assert rows[0]["code"] == "prefer_fire_clearance"
    all_rows = store.load_skill_lessons(limit=5)
    reinforced = next(row for row in all_rows if row["code"] == "compare_alternatives")
    assert reinforced["observations"] == 2
    assert store.load_skill_lessons(limit=5, mode="live")[0]["mode"] == "live"

    # Demo privacy cleanup removes participant data but keeps canonical lesson
    # codes, which contain no address, geometry, prompt, or household details.
    store.purge_all()
    assert store.skill_lesson_count(mode="replay") == 2
    assert store.skill_lesson_count(mode="live") == 1

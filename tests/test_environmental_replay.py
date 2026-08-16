from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from app import replay


def test_openaq_replay_materializes_a_current_iso_timestamp():
    replay.clear_cache()
    result = replay.lookup("https://api.openaq.org/v3/locations/101/latest?limit=100")

    assert result is not None
    observed = result.json()["results"][0]["datetime"]["utc"]
    parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    assert "__REPLAY" not in result.body
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 5


def test_firms_replay_materializes_the_csv_clock_without_changing_shape():
    replay.clear_cache()
    result = replay.lookup(
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/replay/"
        "VIIRS_NOAA20_NRT/-118,47,-117,48/1"
    )

    assert result is not None
    rows = list(csv.DictReader(io.StringIO(result.body)))
    assert len(rows) == 1
    assert rows[0]["acq_date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert len(rows[0]["acq_time"]) == 4
    assert "__REPLAY" not in result.body

"""Local storage: raw files on disk are the source of truth, SQLite is the index.

Every source (Training Hub now, the ESP32 bridge later) writes through here,
so the HA side never has to know where the data came from.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id          TEXT PRIMARY KEY,     -- COROS labelId
    source      TEXT NOT NULL,        -- 'traininghub' | 'ble'
    sport_type  INTEGER,
    name        TEXT,
    start_ts    INTEGER,              -- unix seconds
    end_ts      INTEGER,
    duration_s  INTEGER,
    distance_m  REAL,
    fit_path    TEXT,                 -- relative to the files dir, NULL until downloaded
    raw         TEXT NOT NULL,        -- full JSON from the source
    synced_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_start ON activity(start_ts);

CREATE TABLE IF NOT EXISTS detail (     -- sport-specific extras the FIT lacks (climbing routes)
    id   TEXT PRIMARY KEY,
    raw  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot (   -- daily metrics (EvoLab etc.), one row per kind per day
    day     TEXT NOT NULL,              -- YYYY-MM-DD
    kind    TEXT NOT NULL,              -- 'analyse' ...
    raw     TEXT NOT NULL,
    PRIMARY KEY (day, kind)
);
"""


class Store:
    def __init__(self, db_path: Path, files_dir: Path):
        self.files = files_dir
        (self.files / "fit").mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---------------------------------------------------------- activities
    def has_activity(self, aid: str) -> bool:
        return self.db.execute("SELECT 1 FROM activity WHERE id=?", (aid,)).fetchone() is not None

    def needs_fit(self, aid: str) -> bool:
        row = self.db.execute("SELECT fit_path FROM activity WHERE id=?", (aid,)).fetchone()
        return row is None or row["fit_path"] is None

    def upsert_activity(self, a: dict, source: str) -> None:
        start, end = a.get("startTime"), a.get("endTime")
        self.db.execute(
            """INSERT INTO activity (id, source, sport_type, name, start_ts, end_ts,
                                     duration_s, distance_m, raw, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 sport_type=excluded.sport_type, name=excluded.name,
                 start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                 duration_s=excluded.duration_s, distance_m=excluded.distance_m,
                 raw=excluded.raw, synced_at=excluded.synced_at""",
            (str(a["labelId"]), source, a.get("sportType"), a.get("name"), start, end,
             a.get("workoutTime") or a.get("totalTime"), a.get("distance"),
             json.dumps(a), int(time.time())))
        self.db.commit()

    def save_fit(self, aid: str, start_ts: int | None, content: bytes) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(start_ts or time.time()))
        rel = f"fit/{day}_{aid}.fit"
        (self.files / rel).write_bytes(content)
        self.db.execute("UPDATE activity SET fit_path=? WHERE id=?", (rel, aid))
        self.db.commit()

    def latest_activity(self) -> dict | None:
        row = self.db.execute("SELECT * FROM activity ORDER BY start_ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def distance_since(self, ts: int) -> tuple[float, int]:
        row = self.db.execute(
            "SELECT COALESCE(SUM(distance_m),0) d, COUNT(*) n FROM activity WHERE start_ts>=?",
            (ts,)).fetchone()
        return row["d"], row["n"]

    def missing_details(self, sport_types) -> list[tuple[str, int]]:
        marks = ",".join("?" * len(sport_types))
        return [(r["id"], r["sport_type"]) for r in self.db.execute(
            f"SELECT id, sport_type FROM activity WHERE sport_type IN ({marks}) "
            "AND id NOT IN (SELECT id FROM detail) ORDER BY start_ts DESC", tuple(sport_types))]

    def save_detail(self, aid: str, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO detail (id, raw) VALUES (?,?)", (aid, json.dumps(data)))
        self.db.commit()

    # ----------------------------------------------------------- snapshots
    def save_snapshot(self, kind: str, data, day: str | None = None, commit: bool = True) -> None:
        day = day or time.strftime("%Y-%m-%d")
        self.db.execute("INSERT OR REPLACE INTO snapshot (day, kind, raw) VALUES (?,?,?)",
                        (day, kind, json.dumps(data)))
        if commit:
            self.db.commit()

    def save_days(self, day_list: list[dict]) -> int:
        """EvoLab dayList rows, keyed by their own date (happenDay = 20261122)."""
        n = 0
        for d in day_list:
            hd = str(d.get("happenDay", ""))
            if len(hd) == 8:
                self.save_snapshot("day", d, f"{hd[:4]}-{hd[4:6]}-{hd[6:]}", commit=False)
                n += 1
        self.db.commit()
        return n

    def latest_day_value(self, field: str, max_age_days: int = 7):
        """(value, day) of the newest recent day that has this field; today's row
        exists before the night's values are in. COROS uses 0 / -1 for "no data"."""
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - max_age_days * 86400))
        for row in self.db.execute("SELECT day, raw FROM snapshot WHERE kind='day' AND day>=? "
                                   "ORDER BY day DESC", (since,)):
            v = json.loads(row["raw"]).get(field)
            if v not in (None, 0, -1):
                return v, row["day"]
        return None, None

    def latest_snapshot(self, kind: str) -> dict | None:
        row = self.db.execute("SELECT raw FROM snapshot WHERE kind=? ORDER BY day DESC LIMIT 1",
                              (kind,)).fetchone()
        return json.loads(row["raw"]) if row else None

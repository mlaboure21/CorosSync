"""Tiny web server for the activity viewer, shown in HA through ingress.

HA ingress proxies /api/hassio_ingress/<token>/... to this port, so the page
only ever uses relative URLs.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from climb import CLIMB_TYPES, climb_data
from fitparse_lite import parse_fit

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# COROS sport codes; unknown codes show as "Sport <code>".
SPORTS = {100: "Run", 101: "Indoor run", 102: "Trail run", 103: "Track run", 104: "Hike",
          105: "Mountain climb", 200: "Bike", 201: "Indoor bike", 203: "Gravel bike",
          204: "MTB", 300: "Pool swim", 301: "Open water", 400: "Gym cardio",
          402: "Strength", 500: "Triathlon", 702: "Whitewater", 800: "Indoor climb",
          801: "Bouldering", 802: "Outdoor climb", 900: "Walk", 902: "Stair climb", 904: "Yoga",
          10003: "Outdoor climb"}
GROUPS = {"run": range(100, 104), "hike": (104, 105, 900, 902), "bike": range(200, 300),
          "swim": range(300, 400), "climb": CLIMB_TYPES, "water": range(700, 800),
          "gym": (*range(400, 500), 904)}
CACHE_VERSION = 2  # bump when parse_fit() output changes


def sport_name(code) -> str:
    return SPORTS.get(code, f"Sport {code}")


def sport_group(code) -> str:
    return next((g for g, codes in GROUPS.items() if code in codes), "other")


class Viewer:
    def __init__(self, db_path: Path, files_dir: Path, cache_dir: Path):
        self.db_path, self.files, self.cache = db_path, files_dir, cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        for old in self.cache.glob("*.json"):
            if not old.name.endswith(f".v{CACHE_VERSION}.json"):
                old.unlink()

    def _cached(self, aid: str) -> Path:
        return self.cache / f"{aid}.v{CACHE_VERSION}.json"

    def _db(self):
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def activities(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT a.id, a.sport_type, a.name, a.start_ts, a.duration_s, a.distance_m, "
                              "a.fit_path IS NOT NULL AS has_fit, d.raw AS detail FROM activity a "
                              "LEFT JOIN detail d ON d.id = a.id ORDER BY a.start_ts DESC").fetchall()
        out = []
        for r in rows:
            a = dict(r)
            detail = a.pop("detail")
            a["sport"], a["group"] = sport_name(a["sport_type"]), sport_group(a["sport_type"])
            if detail:
                c = climb_data(a["sport_type"], json.loads(detail))
                a["climb"] = {k: c[k] for k in ("routes", "top_grade", "system")}
                # indoor tables lack the two lowest outdoor entries; shift so both compare
                a["climb"]["rank"] = None if c["top_index"] is None else \
                    c["top_index"] + (2 if a["sport_type"] == 800 else 0)
            out.append(a)
        return out

    def activity(self, aid: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM activity WHERE id=?", (aid,)).fetchone()
            detail = db.execute("SELECT raw FROM detail WHERE id=?", (aid,)).fetchone()
        if not row:
            return None
        out = {k: row[k] for k in ("id", "name", "sport_type", "start_ts", "duration_s", "distance_m")}
        out["sport"], out["group"] = sport_name(row["sport_type"]), sport_group(row["sport_type"])
        raw = json.loads(row["raw"])
        out["raw"] = {k: raw.get(k) for k in ("avgHr", "avgCadence", "step", "trainingLoad", "swolf",
                                              "adjustedPace", "avgStrkRate")}
        if detail:
            out["climb"] = climb_data(row["sport_type"], json.loads(detail["raw"]))
        if row["fit_path"]:
            cached = self._cached(aid)
            if cached.exists():
                out["fit"] = json.loads(cached.read_text())
            else:
                try:
                    out["fit"] = parse_fit(self.files / row["fit_path"])
                    cached.write_text(json.dumps(out["fit"], default=str))
                except Exception as e:  # noqa: BLE001 - a bad file must not kill the page
                    log.warning("Could not parse %s: %s", row["fit_path"], e)
                    out["fit_error"] = str(e)
        return out


    def warm_cache(self) -> int:
        """Pre-parse FIT files so opening an activity is instant (slow on a Pi)."""
        with self._db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM activity WHERE fit_path IS NOT NULL")]
        n = 0
        for aid in ids:
            if not self._cached(aid).exists():
                self.activity(aid)
                n += 1
        return n


def make_handler(viewer: Viewer, allowed: set[str] | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the add-on log quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code=200):
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def do_GET(self):
            if allowed and self.client_address[0] not in allowed:
                return self._json({"error": "forbidden"}, 403)
            path = self.path.split("?")[0]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api/activities":
                    return self._json(viewer.activities())
                m = re.fullmatch(r"/api/activity/([\w-]+)", path)
                if m:
                    a = viewer.activity(m.group(1))
                    return self._json(a) if a else self._json({"error": "not found"}, 404)
                self._json({"error": "not found"}, 404)
            except sqlite3.OperationalError as e:  # DB not created yet on first start
                self._json({"error": str(e)}, 503)

    return Handler


def start(viewer: Viewer, port: int = 8099, allowed: set[str] | None = None) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(viewer, allowed))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Viewer listening on :%d", port)

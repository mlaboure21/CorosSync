"""CorosSync: pull COROS Training Hub data to local storage and publish it to HA.

Run modes:
  python main.py           -> loop forever (add-on mode, reads /data/options.json)
  python main.py once      -> one sync pass, then exit
  python main.py probe     -> log in and dump raw API responses to ./probe/ (for
                              discovering field names; never run inside HA)
Outside the add-on, config comes from env vars COROS_EMAIL, COROS_PASSWORD,
COROS_REGION, DATA_DIR, FILES_DIR, MQTT_HOST/PORT/USER/PASSWORD.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from coros_client import CorosClient, CorosError
from climb import CLIMB_TYPES, trim_detail
from store import Store

log = logging.getLogger("corossync")


def load_config() -> dict:
    cfg = {
        "email": os.getenv("COROS_EMAIL"), "password": os.getenv("COROS_PASSWORD"),
        "region": os.getenv("COROS_REGION", "eu"), "sync_interval_hours": 6,
        "backfill": False, "download_fit": True,
    }
    opts = Path("/data/options.json")
    if opts.exists():
        cfg.update(json.loads(opts.read_text()))
    cfg["data_dir"] = Path(os.getenv("DATA_DIR", "/data"))
    cfg["files_dir"] = Path(os.getenv("FILES_DIR", "/share/corossync"))
    cfg["mqtt"] = {k: os.getenv(f"MQTT_{k.upper()}") for k in ("host", "port", "user", "password")}
    if not cfg["email"] or not cfg["password"]:
        sys.exit("COROS email/password not configured")
    return cfg


# ------------------------------------------------------------------- sync
def sync_activities(client: CorosClient, store: Store, backfill: bool, download_fit: bool) -> int:
    """Walk activities newest-first. Normally stop at the first already-complete
    one; with backfill, walk the whole history once."""
    new = 0
    for a in client.iter_activities():
        aid = str(a["labelId"])
        complete = store.has_activity(aid) and not (download_fit and store.needs_fit(aid))
        if complete and not backfill:
            break
        store.upsert_activity(a, source="traininghub")
        if download_fit and store.needs_fit(aid):
            try:
                store.save_fit(aid, a.get("startTime"), client.download(aid, a["sportType"]))
                log.info("Saved FIT for %s (%s)", a.get("name"), aid)
            except (CorosError, OSError) as e:
                log.warning("FIT download failed for %s: %s", aid, e)
        new += not complete
    return new


def sync_details(client: CorosClient, store: Store) -> int:
    """Fetch route/grade details for climbs that do not have them yet."""
    n = 0
    for aid, sport in store.missing_details(CLIMB_TYPES):
        try:
            store.save_detail(aid, trim_detail(client.activity_detail(aid, sport)))
            n += 1
        except (CorosError, OSError) as e:
            log.warning("Climb detail failed for %s: %s", aid, e)
        time.sleep(0.3)  # be gentle with COROS on the first run
    return n


def publish(store: Store, ha, dash: dict | None) -> None:
    def iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None

    def pos(v):  # COROS uses 0 / -1 for "no data"
        return v if v not in (None, 0, -1) else None

    last = store.latest_activity()
    if last:
        raw = json.loads(last["raw"])
        attrs = {"sport_type": raw.get("sportType"), "device": raw.get("device"),
                 "calories_kcal": round(raw.get("calorie", 0) / 1000),
                 # COROS lists indoor climbing ascent in dm, everything else in m
                 "ascent_m": (raw.get("ascent") or 0) / (10 if raw.get("sportType") in (800, 801) else 1), "steps": raw.get("step"),
                 "coros_id": last["id"]}
        ha.publish("last_activity", last["name"] or "Activity", attrs)
        ha.publish("last_activity_distance", round((last["distance_m"] or 0) / 1000, 2))
        ha.publish("last_activity_duration", round((last["duration_s"] or 0) / 60, 1))
        ha.publish("last_activity_start", iso(last["start_ts"]))
        ha.publish("last_activity_avg_hr", raw.get("avgHr") or None)
        ha.publish("last_activity_load", raw.get("trainingLoad"))
    dist, count = store.distance_since(int(time.time()) - 7 * 86400)
    ha.publish("week_distance", round(dist / 1000, 2))
    ha.publish("week_count", count)

    day = store.latest_snapshot("day")
    if day:
        for key, field in (("resting_hr", "rhr"), ("sleep_hrv", "avgSleepHrv"),
                           ("sleep_hrv_baseline", "sleepHrvBase")):
            value, on = store.latest_day_value(field)
            ha.publish(key, value, {"day": on})
        ha.publish("fatigue", day.get("tiredRate"),
                   {"fatigue_new": day.get("tiredRateNew"), "state": day.get("tiredRateStateNew")})
        ha.publish("acute_load", day.get("ati"))
        ha.publish("chronic_load", day.get("cti"))
        ha.publish("load_ratio", day.get("trainingLoadRatio"),
                   {"state": day.get("trainingLoadRatioState")})
        ha.publish("load_7d", day.get("t7d"), {"load_28d": day.get("t28d"),
                   "recommended_min": day.get("recomendTlMin"), "recommended_max": day.get("recomendTlMax")})
    s = (dash or {}).get("summaryInfo") or {}
    if s:
        ha.publish("recovery", s.get("recoveryPct"), {"state": s.get("recoveryState")})
        ha.publish("full_recovery", s.get("fullRecoveryHours"))
        ha.publish("stamina_level", s.get("staminaLevel"), {
            "ranking_pct": s.get("staminaLevelRanking"), "aerobic_endurance": s.get("aerobicEnduranceScore"),
            "lactate_threshold": s.get("lactateThresholdCapacityScore"),
            "anaerobic_endurance": s.get("anaerobicEnduranceScore"),
            "anaerobic_capacity": s.get("anaerobicCapacityScore")})
        ha.publish("threshold_hr", pos(s.get("lthr")))
        lt = s.get("ltsp")  # seconds per km
        ha.publish("threshold_pace", f"{lt // 60}:{lt % 60:02d} /km" if lt else None)
    ha.publish("last_sync", iso(time.time()))


def sync_once(cfg, client, store, ha) -> None:
    if not client.token:
        client.login()
    n = sync_activities(client, store, cfg["backfill"], cfg["download_fit"])
    log.info("Activities: %d new/updated", n)
    log.info("Climb details: %d fetched", sync_details(client, store))
    dash = None
    try:
        days = store.save_days(client.analyse().get("dayList") or [])
        log.info("Daily metrics: %d days stored", days)
    except CorosError as e:
        log.warning("EvoLab query failed: %s", e)
    try:
        dash = client.dashboard()
        store.save_snapshot("dashboard", dash)
    except CorosError as e:
        log.warning("Dashboard query failed: %s", e)
    if ha:
        publish(store, ha, dash)


def probe(cfg) -> None:
    out = Path("probe"); out.mkdir(exist_ok=True)
    c = CorosClient(cfg["email"], cfg["password"], cfg["region"]); c.login()
    page = c.activities(1, 5)
    calls = {"activities": lambda: page, "analyse": c.analyse, "dashboard": c.dashboard,
             "profile": c.profile}
    if page.get("dataList"):
        a = page["dataList"][0]
        calls["detail"] = lambda: c.activity_detail(str(a["labelId"]), a["sportType"])
    for name, fn in calls.items():
        try:
            (out / f"{name}.json").write_text(json.dumps(fn(), indent=2, ensure_ascii=False))
            print("wrote", out / f"{name}.json")
        except Exception as e:  # noqa: BLE001 - probing, report everything
            print(name, "failed:", e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    cfg = load_config()
    if mode == "probe":
        return probe(cfg)

    store = Store(cfg["data_dir"] / "coros.db", cfg["files_dir"])
    client = CorosClient(cfg["email"], cfg["password"], cfg["region"])
    ha = None
    if cfg["mqtt"]["host"]:
        from mqtt_ha import HAPublisher
        m = cfg["mqtt"]
        ha = HAPublisher(m["host"], int(m["port"] or 1883), m["user"], m["password"])
    else:
        log.warning("No MQTT broker configured: data is stored but not sent to HA")

    viewer = None
    if mode == "loop":
        import web
        viewer = web.Viewer(cfg["data_dir"] / "coros.db", cfg["files_dir"], cfg["data_dir"] / "cache")
        # In the add-on only the Supervisor's ingress proxy may talk to the viewer:
        # ingress does the HA login check, so a direct connection would bypass it.
        web.start(viewer, int(os.getenv("VIEWER_PORT", "8099")),
                  {"172.30.32.2"} if Path("/data/options.json").exists() else None)

    while True:
        try:
            sync_once(cfg, client, store, ha)
            cfg["backfill"] = False  # history only needs walking once per start
            if viewer:
                log.info("Viewer cache: %d activities pre-parsed", viewer.warm_cache())
        except Exception:  # keep the add-on alive across network/API hiccups
            log.exception("Sync failed")
        if mode == "once":
            return
        time.sleep(float(cfg["sync_interval_hours"]) * 3600)


if __name__ == "__main__":
    main()

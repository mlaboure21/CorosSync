"""Turn a FIT file into what the viewer needs: a GPS track, time series, laps, summary."""

from __future__ import annotations

import warnings

import fitdecode

# COROS writes some fields with odd sizes; fitdecode warns but decodes fine.
warnings.filterwarnings("ignore", module="fitdecode")

SEMI = 180 / 2**31  # FIT stores lat/lon in semicircles

FIELDS = {  # FIT record field -> output key
    "heart_rate": "hr", "enhanced_speed": "speed", "speed": "speed",
    "enhanced_altitude": "alt", "altitude": "alt", "cadence": "cad",
    "power": "power", "distance": "dist",
}


def parse_fit(path, max_points: int = 1500) -> dict:
    t0 = None
    series = {k: [] for k in ("t", "hr", "speed", "alt", "cad", "power", "dist")}
    track, laps, lengths, summary = [], [], [], {}
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                vals = {f.name: f.value for f in frame.fields if f.value is not None}
                ts = vals.get("timestamp")
                if ts is None:
                    continue
                t0 = t0 or ts
                series["t"].append(int((ts - t0).total_seconds()))
                row = {}
                for src, dst in FIELDS.items():
                    if src in vals and dst not in row:
                        row[dst] = vals[src]
                for k in ("hr", "speed", "alt", "cad", "power", "dist"):
                    series[k].append(row.get(k))
                lat, lon = vals.get("position_lat"), vals.get("position_long")
                if lat is not None and lon is not None:
                    track.append([round(lat * SEMI, 6), round(lon * SEMI, 6)])
            elif frame.name == "lap":
                v = {f.name: f.value for f in frame.fields}
                laps.append({"time_s": v.get("total_timer_time"), "dist_m": v.get("total_distance"),
                             "avg_hr": v.get("avg_heart_rate"), "ascent": v.get("total_ascent"),
                             "power": v.get("avg_power"),
                             "avg_speed": v.get("enhanced_avg_speed") or v.get("avg_speed")})
            elif frame.name == "length":  # pool swimming
                v = {f.name: f.value for f in frame.fields}
                if str(v.get("length_type")) == "active":
                    lengths.append({"time_s": v.get("total_timer_time"), "strokes": v.get("total_strokes"),
                                    "stroke": str(v["swim_stroke"]) if v.get("swim_stroke") is not None else None})
            elif frame.name == "session":
                v = {f.name: f.value for f in frame.fields}
                summary = {k: v.get(k) for k in (
                    "total_timer_time", "total_elapsed_time", "total_distance", "total_calories",
                    "avg_heart_rate", "max_heart_rate", "total_ascent", "total_descent", "avg_power",
                    "avg_temperature", "avg_step_length", "avg_stance_time", "avg_vertical_oscillation",
                    "avg_vertical_ratio", "pool_length")}
                summary["max_speed"] = v.get("enhanced_max_speed") or v.get("max_speed")
                summary["floors"] = v.get("Floors")
                for k in ("sport", "sub_sport"):
                    summary[k] = str(v[k]) if v.get(k) is not None else None

    # Downsample so the browser stays fast on long rides.
    n = len(series["t"])
    if n > max_points:
        step = n / max_points
        idx = [int(i * step) for i in range(max_points)]
        series = {k: [v[i] for i in idx] for k, v in series.items()}
    if len(track) > max_points:
        step = len(track) / max_points
        track = [track[int(i * step)] for i in range(max_points)] + [track[-1]]
    # Drop series the device never recorded.
    series = {k: v for k, v in series.items() if k == "t" or any(x is not None for x in v)}
    return {"summary": summary, "track": track, "series": series, "laps": laps, "lengths": lengths}

"""COROS climbing data (routes, grades, styles) from /activity/detail/query.

Grade tables and style names are the ones the Training Hub web app uses: `hardestGrade`
is an index into the table of `gradingSystem`, and 255 means not recorded. Indoor climbing
and bouldering use shorter tables than outdoor climbing. Times are 1/100 s, heights are m.
"""

from __future__ import annotations

CLIMB_TYPES = (800, 801, 802, 10003)  # indoor, bouldering, outdoor, outdoor (older firmware)
INDOOR = (800, 801)
REST = 3

_YDS = ["5.6", "5.7", "5.8", "5.9"] + [f"5.{n}{c}" for n in range(10, 15) for c in "abcd"] + ["5.15a"]
_FRENCH = ["4a", "4b", "4c", "5a", "5b", "5c", "6a", "6a+", "6b", "6b+", "6c", "6c+", "7a", "7a+",
           "7b", "7b+", "7c", "7c+", "8a", "8a+", "8b", "8b+", "8c", "8c+", "9a", "9a+", "9b", "9b+"]
_UIAA = ["IV", "IV+", "V", "V+"] + [f"{n}{s}" for n in ("VI", "VII", "VIII", "IX", "X", "XI")
                                    for s in ("-", "", "+")] + ["XII-", "XII"]
_FONT = ["4", "5", "5+", "6A", "6A+", "6B", "6B+", "6C", "6C+", "7A", "7A+", "7B", "7B+", "7C",
         "7C+", "8A", "8A+", "8B", "8B+", "8C", "8C+", "9A"]
_JUG = "Jug the rope"
_V = [f"V{i}" for i in range(18)]
_EWBANK = [str(i) for i in range(12, 40)]

INDOOR_GRADES = {1: _YDS, 2: _FRENCH, 7: _V, 8: _FONT, 9: _UIAA, 10: _EWBANK}
OUTDOOR_GRADES = {
    1: [_JUG, "Low 5th"] + _YDS, 2: [_JUG, "Easy"] + _FRENCH,
    3: [_JUG] + [f"A{i}" for i in range(1, 6)], 4: [_JUG] + [f"WI{i}" for i in range(1, 8)],
    5: [_JUG] + [f"M{i}" for i in range(1, 17)],
    6: [_JUG, "VD", "S", "HS", "VS", "HVS"] + [f"E{i}" for i in range(1, 13)],
    9: [_JUG, "Easy"] + _UIAA, 10: [_JUG, "Easy"] + _EWBANK,
}
STYLES = {0: "Redpoint", 1: "Onsight", 2: "Flash", 3: "Attempt", 5: "Repeat"}
SENT = {0, 1, 2, 5}


def grade(sport_type: int, system, index) -> str | None:
    if index is None or system is None:
        return None
    table = (INDOOR_GRADES if sport_type in INDOOR else OUTDOOR_GRADES).get(system)
    return table[index] if table and 0 <= index < len(table) else None


def trim_detail(d: dict) -> dict:
    """Drop the big per-second graphs (the FIT has those) and the account info."""
    keep = {"summary": d.get("summary"), "lapList": d.get("lapList"),
            "overall": d.get("overall"), "weather": d.get("weather")}
    keep["sportDataDetailList"] = [{"summary": x.get("summary")} for x in d.get("sportDataDetailList") or []]
    return keep


def _sec(v):
    return round(v / 100, 1) if v else None


def _attempts(sport_type: int, items: list[dict]) -> list[dict]:
    """One dict per climb attempt, in order; rests become `rest_s` of the attempt before."""
    out: list[dict] = []
    for x in items:
        if sport_type == 802:  # each stage comes as route (1) + pitch (2) + phase (3) rows
            if x.get("mode") != 1 or x.get("sportStage") != 1:
                continue
            route = x.get("routeIndex")
        else:
            if x.get("mode") == REST:
                if out:
                    out[-1]["rest_s"] = _sec(x.get("time"))
                continue
            route = x.get("rowIndex")
        falls = x.get("falls") or 0
        out.append({
            "route": route, "grade": grade(sport_type, x.get("gradingSystem"), x.get("hardestGrade")),
            "grade_index": x.get("hardestGrade"), "style": STYLES.get(x.get("style")),
            "sent": x.get("style") in SENT, "time_s": _sec(x.get("time")), "height_m": x.get("elevGain"),
            "falls": falls, "max_fall_m": round((x.get("maxActivityFall") or 0) / 100, 1) if falls else None,
            "avg_hr": x.get("avgHr") or None, "max_hr": x.get("maxHr") or None, "rest_s": None})
    for a in out:
        if a["grade"] is None:  # COROS shows no style without a grade either
            a["sent"] = False
    return out


def climb_data(sport_type: int, d: dict) -> dict:
    s = d.get("summary") or {}
    lists = {x.get("type"): x.get("lapItemList") or [] for x in d.get("lapList") or []}
    attempts = _attempts(sport_type, lists.get(2, [])) if sport_type != 10003 else []
    stages = [x["summary"] for x in d.get("sportDataDetailList") or [] if x.get("summary")]
    if sport_type == 10003:  # older multi-pitch recordings: summary per stage only
        s = next((x for x in stages if x.get("mutliPitchStage", 1) == 1 and x.get("gradingSystem")), s)

    routes: dict = {}  # best graded attempt of each route
    for a in attempts:
        r = routes.setdefault(a["route"], a)
        if a["grade"] and (not r["grade"] or (a["sent"], a["grade_index"]) > (r["sent"], r["grade_index"])):
            routes[a["route"]] = a
    sent = [a for a in routes.values() if a["sent"]]
    best = max(sent, key=lambda a: a["grade_index"], default=None)
    pyramid: dict = {}
    for a in sorted((a for a in routes.values() if a["grade"]), key=lambda a: -a["grade_index"]):
        p = pyramid.setdefault(a["grade"], {"grade": a["grade"], "sent": 0, "other": 0})
        p["sent" if a["sent"] else "other"] += 1
    top = grade(sport_type, s.get("gradingSystem"), s.get("hardestGrade"))
    return {
        "routes": (s.get("totalRouteNum") if sport_type == 802 else None) or s.get("pitch") or len(routes) or None,
        "top_grade": top, "top_index": s.get("hardestGrade") if top else None,
        "system": s.get("gradingSystem"), "best_sent": best["grade"] if best else None,
        "falls": s.get("falls"), "max_fall_m": round(s["maxActivityFall"] / 100, 1) if s.get("maxActivityFall") else None,
        "climbing_s": _sec(s.get("climbingTime")), "ascent_m": s.get("climbElevGain") or s.get("elevGain"),
        "aerobic_effect": s.get("aerobicEffect") or None,
        "attempts": attempts, "pyramid": list(pyramid.values())}

# CorosSync

A Home Assistant app (add-on) that copies your COROS watch data from COROS Training Hub to local
storage: original FIT files, sensors through MQTT, and an activity viewer in the HA sidebar.

Unofficial and not affiliated with COROS. It uses the private API behind training.coros.com, which
can change or stop working at any time. Your COROS login is kept in the app's configuration and is
only sent to COROS. Data still goes through the COROS cloud first.

## Plan
1. **Training Hub sync** (done): FIT files + SQLite, sensors via MQTT discovery.
2. **Activity viewer** (done): filters, GPS map, charts, sport-specific data such as climbing routes.
3. **Local Bluetooth sync** (idea): read the watch directly instead of the cloud. Only the source
   changes; storage and HA stay the same.

## Install
Needs an MQTT broker (the Mosquitto app). In the HA app store, open the menu > Repositories and add
`https://github.com/mlaboure21/CorosSync`, then install CorosSync, enter your COROS login and start it.
Each release bumps `version` in `corossync/config.yaml` and adds to `CHANGELOG.md`.

## Layout
```
repository.yaml          HA add-on repository manifest
corossync/               the add-on
  config.yaml            options: email, password, region, interval, backfill, download_fit
  app/coros_client.py    Training Hub client (login, activities, FIT download, EvoLab)
  app/climb.py           climbing routes, grades and styles
  app/store.py           SQLite index (/data/coros.db) + raw files (/share/corossync/fit)
  app/mqtt_ha.py         MQTT discovery -> "COROS Watch" device in HA
  app/web.py + static/   viewer (ingress port 8099): list, Leaflet map, HR/pace/alt charts, laps
  app/fitparse_lite.py   FIT -> track/series/laps/summary (cached in /data/cache)
  app/main.py            sync loop; `once` and `probe` modes for testing
```

## Testing outside HA
```
cd corossync/app && pip install -r ../requirements.txt
export COROS_EMAIL=... COROS_PASSWORD=... DATA_DIR=./tmp FILES_DIR=./tmp/files
python main.py probe   # dumps raw API JSON to ./probe/ to learn field names
python main.py once    # one full sync, no MQTT unless MQTT_HOST is set
```

## What the Training Hub API gives (checked November 2026, EU account)
- `/activity/query` (GET): times in unix s, distance m, workoutTime s, calorie = cal*1000,
  avgHr, trainingLoad, step.
- `/activity/detail/download` (POST, fileType=4): FIT file URL. Parses with fitdecode.
- `/analyse/query` (GET): `dayList` = 84 days of rhr, avgSleepHrv, sleepHrvBase, ati, cti,
  tiredRate, trainingLoadRatio, t7d, t28d.
- `/dashboard/query` (GET): recoveryPct, fullRecoveryHours, staminaLevel, lthr, ltsp.
- `/activity/detail/query` (POST): climbing routes (grade index, style, falls, height; times in
  1/100 s). Grade tables and styles in `app/climb.py` come from the Training Hub web app.
- `/activity/query` ascent is in dm for indoor climbing/bouldering, m for the rest.
- **Not here**: sleep stages and daily steps; COROS exposes those through other APIs.

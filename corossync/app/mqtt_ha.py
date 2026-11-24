"""Publish CorosSync state to Home Assistant through MQTT discovery."""

from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

DEVICE = {"identifiers": ["corossync"], "name": "COROS Watch",
          "manufacturer": "COROS", "model": "APEX 2 Pro (via CorosSync)"}
BASE = "corossync"

# key -> (name, unit, device_class, state_class, icon)
SENSORS = {
    "last_activity":          ("Last activity", None, None, None, "mdi:run"),
    "last_activity_distance": ("Last activity distance", "km", "distance", None, "mdi:map-marker-distance"),
    "last_activity_duration": ("Last activity duration", "min", "duration", None, "mdi:timer-outline"),
    "last_activity_start":    ("Last activity start", None, "timestamp", None, "mdi:calendar"),
    "week_distance":          ("Distance last 7 days", "km", "distance", "measurement", "mdi:chart-line"),
    "week_count":             ("Activities last 7 days", None, None, "measurement", "mdi:counter"),
    "last_activity_avg_hr":   ("Last activity avg HR", "bpm", None, None, "mdi:heart"),
    "last_activity_load":     ("Last activity training load", None, None, None, "mdi:weight-lifter"),
    "resting_hr":             ("Resting heart rate", "bpm", None, "measurement", "mdi:heart-pulse"),
    "sleep_hrv":              ("Sleep HRV", "ms", None, "measurement", "mdi:heart-flash"),
    "sleep_hrv_baseline":     ("Sleep HRV baseline", "ms", None, "measurement", "mdi:heart-flash"),
    "fatigue":                ("Fatigue", None, None, "measurement", "mdi:battery-heart-variant"),
    "acute_load":             ("Acute training load", None, None, "measurement", "mdi:chart-bell-curve"),
    "chronic_load":           ("Chronic training load", None, None, "measurement", "mdi:chart-bell-curve-cumulative"),
    "load_ratio":             ("Training load ratio", None, None, "measurement", "mdi:scale-balance"),
    "load_7d":                ("Training load 7 days", None, None, "measurement", "mdi:weight-lifter"),
    "recovery":               ("Recovery", "%", None, "measurement", "mdi:battery-charging"),
    "full_recovery":          ("Time to full recovery", "h", "duration", "measurement", "mdi:timer-sand"),
    "stamina_level":          ("Running fitness", None, None, "measurement", "mdi:run-fast"),
    "threshold_hr":           ("Threshold heart rate", "bpm", None, "measurement", "mdi:heart-cog"),
    "threshold_pace":         ("Threshold pace", None, None, None, "mdi:speedometer"),
    "last_sync":              ("Last sync", None, "timestamp", None, "mdi:cloud-sync"),
}


class HAPublisher:
    def __init__(self, host: str, port: int, user: str | None, password: str | None):
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="corossync")
        if user:
            self.c.username_pw_set(user, password)
        self.c.will_set(f"{BASE}/status", "offline", retain=True)
        self.c.on_connect = self._on_connect
        self.c.connect_async(host, port)  # keeps retrying if the broker is not up yet
        self.c.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Runs on every (re)connect: the broker published our 'offline' will meanwhile."""
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        self._discovery()
        self.c.publish(f"{BASE}/status", "online", retain=True)
        log.info("Connected to MQTT")

    def _discovery(self) -> None:
        for key, (name, unit, dclass, sclass, icon) in SENSORS.items():
            cfg = {
                "name": name, "unique_id": f"corossync_{key}", "default_entity_id": f"sensor.coros_{key}",
                "state_topic": f"{BASE}/{key}/state",
                "json_attributes_topic": f"{BASE}/{key}/attributes",
                "availability_topic": f"{BASE}/status", "icon": icon, "device": DEVICE,
            }
            if unit: cfg["unit_of_measurement"] = unit
            if dclass: cfg["device_class"] = dclass
            if sclass: cfg["state_class"] = sclass
            self.c.publish(f"homeassistant/sensor/corossync/{key}/config",
                           json.dumps(cfg), retain=True)

    def publish(self, key: str, state, attributes: dict | None = None) -> None:
        self.c.publish(f"{BASE}/{key}/state", "None" if state is None else str(state), retain=True)
        if attributes is not None:
            self.c.publish(f"{BASE}/{key}/attributes", json.dumps(attributes), retain=True)

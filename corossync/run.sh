#!/usr/bin/with-contenv bashio
# Hand the broker details from the Supervisor (Mosquitto add-on) to the app.
if bashio::services.available "mqtt"; then
  export MQTT_HOST="$(bashio::services mqtt 'host')"
  export MQTT_PORT="$(bashio::services mqtt 'port')"
  export MQTT_USER="$(bashio::services mqtt 'username')"
  export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
else
  bashio::log.warning "No MQTT service found; install the Mosquitto add-on for HA sensors"
fi
cd /app
exec python3 -u main.py

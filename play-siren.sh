#!/bin/bash
# Manual helper. MQTT playback is owned by fpos_mqtt_ha.py (max ~60s).
# Volume percent: first arg, or SIREN_VOLUME, or 100.
VOLUME="${1:-${SIREN_VOLUME:-100}}"
case "$VOLUME" in
  ''|*[!0-9]*) VOLUME=100 ;;
esac
if [ "$VOLUME" -gt 100 ]; then VOLUME=100; fi
pactl set-sink-mute @DEFAULT_SINK@ 0
pactl set-sink-volume @DEFAULT_SINK@ "${VOLUME}%"
timeout 60 paplay /home/haui/siren/siren.mp3
#!/bin/bash
# Make sure Bluetooth is connected and volume is high
pactl set-sink-volume @DEFAULT_SINK@ 100%
paplay /home/haui/siren/siren.mp3
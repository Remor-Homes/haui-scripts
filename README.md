# HAUI

HAUI is a Home Automation User Interface initially designed for Home assistant community but suitable for any home automation system. It's based on a 7in capacitive touch display and a Raspberry Pi using a modified version of FullPageOS kiosk OS.

<img src="https://haui.remorh.com/haui-07/marketing_002.jpg" alt="HAUI_01" style="width:300px;"/>

Find more information about [HAUI here]([url](https://haui.remorh.com/haui-07/)).

# This Repo: HAUI Scripts

This repository contains :
1. the python script that does the MQTT interface between HAUI and home assistant.
2. the two services added to systemctl.
3. the siren playback script invoked from Home Assistant via MQTT.

## fpos_mqtt_ha.py

This script is executed every boot with the systemctl backlight.service. It does three things:

1. Connects to a configured MQTT server using the data on the `.env` file in the same location as the script and creates Home Assistant entities for backlight control, plus a separate MQTT device for the siren.
2. Runs backlight timing locally. If MQTT is down, dim/timeout/touch-wake still use the `.env` defaults.
3. Plays `play-siren.sh` when the HAUI Siren entity is turned on.

### Backlight behaviour

- **Saved brightness** (`SAVED_BRIGHTNESS` / HAUI Backlight Level): level restored on touch or when the HA light is turned on. Idle dimming never overwrites this.
- **Dimming percent**: panel level after the backlight timeout, while unused.
- **Dimming timeout = 600**: the panel never turns off. After the backlight timeout it stays at dimming percent until the next touch.
- **Dimming timeout < 600**: unused → dimming percent → off after that many seconds.
- **Touch while off or dimmed**: restore saved brightness and restart the idle timer.
- **Touch while already at saved brightness**: only restart the idle timer.

## Services

### backlight.service

Is the service that executes fpos_mqtt_ha.py. See above for details. The service is always active.

### chromium-reset.service

Its executed every boot. Chromium needs to reset the hostname when it's changed. Resetting at boot will not cause any side effect for the user and will make sure hostname changes do not cause chromium to get stuck at boot. This service is deactivated after executing at boot.

### Siren

`play-siren.sh` (repo root) plays `/home/haui/siren/siren.mp3`. Home Assistant gets a separate MQTT device named `<hostname> siren` with **HAUI Siren** and **HAUI Siren Volume** (0–100%). Volume is applied to the PulseAudio/Bluetooth sink before the clip plays, persisted as `SIREN_VOLUME` in `.env`. The clip repeats while the toggle is on, **stops on MQTT off**, and **always stops after 60 seconds** even if off is missed.

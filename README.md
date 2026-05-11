# HAUI Scripts

This repository contains :
1. the python script that does the MQTT interface between HAUI and home assistant.
2. the two services added to systemctl.

## fpos_mqtt_ha.py

This script is executed every boot with the systemctl backlignt.service. If does two things:

1. Connects to a configured MQTT server using the data on the .env file in the same location as the script and creates entities for the display backlight control.
2. Does the backlight timing control and changes on touch events. If not connected to the MQTT, it will serve the backlight control with the default settings on the .env file.

## Services

### backlight.service

Is the service that executes fpos_mqtt_ha.py. See above for details. The service is always active.

### chromium-reset.service

Its executed every boot. Chromium needs to reset the hostname when it's changed. Resetting at boot will not cause any side effect for the user and will make sure hostname changes do not cause chromium to get stuck at boot. This service is deactivated after executing at boot.

#!/bin/bash
LOGFILE="/var/log/chromium-locks.log"

echo "[$(date '+%a %d %b %H:%M:%S %Z %Y')] === Chromium lock cleanup STARTED (boot) ===" | tee -a "$LOGFILE"

rm -rf /home/haui/.config/chromium/Singleton* 2>/dev/null
rm -f /home/haui/.config/chromium/Default/.org.chromium.Chromium.* 2>/dev/null

echo "[$(date '+%a %d %b %H:%M:%S %Z %Y')] Chromium locks cleared successfully (boot)" | tee -a "$LOGFILE"

exit 0
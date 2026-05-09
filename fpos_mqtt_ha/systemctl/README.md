# How to setup the service

1. Update backlight.service details using
   - "nano backlight.service
2. Copy it to /etc/systemd/system/backlight.service
3. Start the service
   - sudo systemctl daemon-reload
   - sudo systemctl enable backlight.service
   - sudo systemctl start backlight.service
4. Check status:
   - systemctl status backlight.service
   - journalctl -u backlight.service -f

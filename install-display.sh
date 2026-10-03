#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run: sudo sh install-display.sh" >&2
    exit 1
fi

apt-get update
apt-get install -y python3 python3-pil python3-smbus i2c-tools fonts-dejavu-core
install -d -m 0755 /usr/local/lib/pi3-display
install -m 0755 ./pi3_display.py /usr/local/lib/pi3-display/pi3_display.py
install -m 0644 ./pi3-display.service /etc/systemd/system/pi3-display.service

# Stop the original OLED process started by rc.local and prevent a duplicate.
if [ -f /etc/rc.local ]; then
    sed -i '/Adafruit_Python_SSD1306\/examples\/stats.py/s/^/# disabled by pi3-display: /' /etc/rc.local
fi
pkill -f 'Adafruit_Python_SSD1306/examples/stats.py' 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now pi3-display.service
echo "Pi 3 OLED display installed and started."

#!/usr/bin/env python3
"""Pi 3 OLED display with the same two-line pages used by the Pi 4 project."""

from __future__ import print_function

import datetime
import json
import os
import socket
import subprocess
import time

from PIL import Image, ImageDraw, ImageFont

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus


I2C_BUS = 1
OLED_ADDRESS = 0x3C
BATTERY_ADDRESS = 0x66
BATTERY_REGISTER = 0x01
WIDTH = 128
HEIGHT = 32
PAGE_SECONDS = 3
STATE_FILE = "/var/lib/pi3-display/battery-state.json"
FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


class SSD1306(object):
    def __init__(self):
        self.bus = SMBus(I2C_BUS)
        self.command(0xAE, 0xD5, 0x80, 0xA8, HEIGHT - 1, 0xD3, 0x00,
                     0x40, 0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA,
                     0x02, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4,
                     0xA6, 0xAF)

    def command(self, *values):
        for value in values:
            self.bus.write_byte_data(OLED_ADDRESS, 0x00, value)

    def show(self, image):
        self.command(0x21, 0, WIDTH - 1, 0x22, 0, HEIGHT // 8 - 1)
        pixels = image.load()
        data = []
        for page in range(HEIGHT // 8):
            for x in range(WIDTH):
                value = 0
                for bit in range(8):
                    if pixels[x, page * 8 + bit]:
                        value |= 1 << bit
                data.append(value)
        for offset in range(0, len(data), 16):
            self.bus.write_i2c_block_data(
                OLED_ADDRESS, 0x40, data[offset:offset + 16])

    def close(self):
        try:
            self.show(Image.new("1", (WIDTH, HEIGHT)))
        finally:
            self.bus.close()


def load_state():
    try:
        with open(STATE_FILE, "r") as handle:
            return json.load(handle)
    except (IOError, ValueError, TypeError):
        return {}


def save_state(state):
    directory = os.path.dirname(STATE_FILE)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(state, handle, separators=(",", ":"))
    os.rename(temporary, STATE_FILE)


def read_battery(bus):
    value = bus.read_byte_data(BATTERY_ADDRESS, BATTERY_REGISTER)
    if value < 0 or value > 100:
        raise ValueError("invalid battery value: {0}".format(value))
    return value


def unbcd(value):
    return (value >> 4) * 10 + (value & 0x0F)


def restore_time_from_rtc(bus):
    """Use PCF8563 when its clock is newer than the system clock."""
    try:
        data = bus.read_i2c_block_data(0x51, 0x02, 7, force=True)
    except TypeError:
        data = bus.read_i2c_block_data(0x51, 0x02, 7)
    rtc = datetime.datetime(2000 + unbcd(data[6] & 0xFF),
                            unbcd(data[5] & 0x1F),
                            unbcd(data[3] & 0x3F),
                            unbcd(data[2] & 0x3F),
                            unbcd(data[1] & 0x7F),
                            unbcd(data[0] & 0x7F))
    if (rtc - datetime.datetime.utcnow()).total_seconds() > 60:
        subprocess.check_call(["date", "-u", "-s",
                               rtc.strftime("%Y-%m-%d %H:%M:%S")])


def update_battery(bus, state, now):
    raw = read_battery(bus)
    status = state.get("status", "discharging")
    pending = state.get("full_since")

    if raw < 100:
        status = "discharging"
        pending = None
        if state.get("discharge_start") is None:
            state["discharge_start"] = now
    elif state.get("last_raw") is not None and state.get("last_raw") < 100:
        pending = now
    elif pending is not None and now - pending >= 3:
        status = "charging"
        state["discharge_start"] = None

    state.update({"percent": raw, "last_raw": raw, "status": status,
                  "full_since": pending, "updated": now})
    save_state(state)
    return raw, status == "charging"


def cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as handle:
        return float(handle.read().strip()) / 1000.0


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except IOError:
        return "no network"
    finally:
        sock.close()


def used_text(state, now):
    started = state.get("discharge_start")
    if started is None:
        return "00 HOUR 00 MINUTS"
    minutes = int(max(0, now - started)) // 60
    return "{0:02d} HOUR {1:02d} MINUTS".format(minutes // 60,
                                                 minutes % 60)


def font(size):
    try:
        return ImageFont.truetype(FONT_FILE, size)
    except IOError:
        return ImageFont.load_default()


def centered_text(draw, y, text):
    current = font(13)
    text_width, text_height = draw.textsize(text, font=current)
    if text_width > WIDTH:
        current = font(max(8, int(13 * WIDTH / text_width)))
        text_width, text_height = draw.textsize(text, font=current)
    draw.text((max(0, (WIDTH - text_width) // 2),
               y + max(0, (16 - text_height) // 2)), text,
              font=current, fill=255)


def frame(lines):
    image = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    centered_text(draw, 0, lines[0])
    centered_text(draw, 16, lines[1])
    return image


def main():
    display = SSD1306()
    battery_bus = SMBus(I2C_BUS)
    try:
        restore_time_from_rtc(battery_bus)
    except (IOError, OSError, ValueError, subprocess.CalledProcessError):
        pass
    state = load_state()
    now = time.time()
    previous_update = state.get("updated")
    discharge_start = state.get("discharge_start")
    if (state.get("status") != "charging" and previous_update is not None
            and discharge_start is not None and now - previous_update > 300):
        state["discharge_start"] = discharge_start + (now - previous_update)
    page = 0
    page_started = time.time()
    battery = state.get("percent", "--")
    charging = state.get("status") == "charging"
    try:
        while True:
            now = time.time()
            try:
                battery, charging = update_battery(battery_bus, state, now)
            except (IOError, ValueError):
                pass
            if now - page_started >= PAGE_SECONDS:
                page = (page + 1) % 4
                page_started = now
            current = datetime.datetime.now()
            pages = [
                (current.strftime("TIME %H:%M:%S"),
                 current.strftime("DATE %m-%d")),
                ("CPU  {0:.1f} C".format(cpu_temperature()),
                 "FAN  AUTO"),
                ("BAT  {0}%".format(battery),
                 "CHARGING" if charging else used_text(state, now)),
                ("IP ADDRESS", local_ip()),
            ]
            display.show(frame(pages[page]))
            time.sleep(1)
    finally:
        battery_bus.close()
        display.close()


if __name__ == "__main__":
    main()

# ABS-SBRR v3 — Automated Bell System for Schools

Raspberry Pi Pico W (RP2350) + MicroPython v1.24+

## Features

- **AP Hotspot mode** — no router/internet dependency, teachers connect directly
- **DS3231 RTC** — keeps accurate time when offline (±2ppm, CR2032 backup)
- **NTP sync** — auto-syncs when school WiFi is available, writes back to RTC
- **Web dashboard** — schedule editor, manual bell ring, live status
- **Watchdog timer** — auto-recover from hangs
- **AES-128 encrypted** credentials at rest

## Hardware

| Component | Pin | Pico W Physical |
|-----------|-----|-----------------|
| Bell relay | GP15 | Pin 20 |
| Manual button | GP14 | Pin 19 |
| Status LED | GP25 | Built-in |
| RTC SDA | GP4 | Pin 6 |
| RTC SCL | GP5 | Pin 7 |
| RTC VCC | 3V3 | Pin 36 |
| RTC GND | GND | Pin 38 |

## Boot Modes

**With internet:** WiFi → NTP sync → writes time to DS3231 → dashboard on LAN IP

**AP-only:** Creates `SBRRBell_AP` hotspot → dashboard at `http://192.168.4.1` → time from RTC

## Configuration

### wifi.json
```json
{
  "ssid": "",
  "password": "",
  "ap_ssid": "SBRRBell_AP",
  "ap_password": "bellsystem"
}
```
Empty `ssid` = AP-only mode. Set school WiFi SSID for STA + NTP fallback.

### config.json
Key settings: `bell_pin`, `i2c_sda`, `i2c_scl`, `timezone_offset` (19800 = IST), `rtc_enabled`, `ntp_interval_hours`.

## Dashboard

Connect to `SBRRBell_AP` → open `http://192.168.4.1` → login (admin/admin123).

## Files

| File | Purpose |
|------|---------|
| `main.py` | Boot sequence + main loop |
| `rtc_sync.py` | DS3231 I2C driver |
| `ntp_sync.py` | NTP/HTTP time sync |
| `wifi_manager.py` | STA/AP WiFi management |
| `bell_controller.py` | Bell relay + patterns |
| `schedule_manager.py` | Schedule logic |
| `web_server.py` | HTTP dashboard server |
| `config_manager.py` | Config + encrypted storage |
| `dashboard.html` | Web UI |

## License

MIT

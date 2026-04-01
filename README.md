# ABS-SBRR v3 — Automated Bell System for Schools

Raspberry Pi Pico 2 W (RP2350) + MicroPython v1.24+

## Features

- **AP Hotspot mode** — no router/internet dependency, teachers connect directly
- **DS3231 RTC** — keeps accurate time when offline (±2ppm, CR2032 backup)
- **NTP sync** — auto-syncs when school WiFi is available, writes back to RTC
- **Web dashboard** — schedule editor, manual bell ring, live status
- **Watchdog timer** — auto-recover from hangs
- **AES-128 encrypted** credentials at rest

## Hardware

### Pico 2 W Pin Assignments

| Component | GPIO | Physical Pin |
|-----------|------|--------------|
| Bell (MOSFET gate) | GP15 | Pin 20 |
| Manual button | GP14 | Pin 19 |
| Status LED | GP25 | Built-in |
| RTC SDA | GP4 | Pin 6 |
| RTC SCL | GP5 | Pin 7 |

### DS3231 RTC Wiring

| DS3231 | Pico 2 W | Physical Pin |
|--------|----------|--------------|
| VCC | 3V3(OUT) | Pin 36 |
| GND | GND | Pin 38 |
| SDA | GP4 | Pin 6 |
| SCL | GP5 | Pin 7 |

### 12V Bell Wiring (MOSFET — IRLZ44N)

```
12V PSU (+) ●───────────────► Bell (+)

12V PSU (−) ●───────────────► IRLZ44N Source ◄──── Pico GND (Pin 38)

Bell (−)    ●───────────────► IRLZ44N Drain

Pico GP15   ●──┤220Ω├──────► IRLZ44N Gate

Flyback diode (1N4007) across Bell terminals:
  Cathode (stripe) → Bell (+)
  Anode            → Bell (−)
```

| Part | Value |
|------|-------|
| MOSFET | IRLZ44N (logic-level N-channel) |
| Gate resistor | 220Ω ¼W |
| Flyback diode | 1N4007 |
| Bell PSU | 12V DC adapter |

**Why MOSFET over relay:** Silent, no mechanical wear, supports fast patterns (double/triple ring), cheaper, smaller.

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

## Security Model

**This is a local-network device.** The web dashboard has no TLS — all traffic including login is plaintext HTTP.

Auth uses a SHA-256 nonce challenge:
- Client never sends the password — it sends `SHA256(user + SHA256(password) + nonce)`
- Server compares against its own computation
- Nonce rotates on each successful login
- Rate limiting: 5 failed attempts → 60s lockout
- Session tokens expire after 24 hours (configurable)

**Threat model:** This protects against casual unauthorized access on the school network. It does NOT protect against:
- Network eavesdroppers (no TLS)
- Replay of captured auth tokens (until nonce rotation or token expiry)
- Physical access to the device

**Recommendation:** Use AP mode (direct hotspot) rather than bridging to the school LAN for better isolation. Change the default password immediately after first boot.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Boot sequence + main loop |
| `rtc_sync.py` | DS3231 I2C driver |
| `ntp_sync.py` | NTP/HTTP time sync |
| `wifi_manager.py` | STA/AP WiFi management |
| `bell_controller.py` | Bell MOSFET control + patterns |
| `schedule_manager.py` | Schedule logic |
| `web_server.py` | HTTP dashboard server |
| `config_manager.py` | Config + encrypted storage |
| `dashboard.html` | Web UI |

## License

MIT

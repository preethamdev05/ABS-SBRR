# SBRR Mahajana Bell Automation System

**Version 1.0.0** — Production-grade automated school bell controller for Raspberry Pi Pico W (RP2350) running MicroPython v1.24+.

Built for the **Department of BCA in Artificial Intelligence**, II Semester 2025–26.

---

## Overview

SBRR is a self-contained, network-connected school bell controller. It runs a full-period timetable on a configurable weekly schedule, fires a physical buzzer at the correct times, and exposes a responsive web dashboard for configuration and monitoring — all on a microcontroller with no external dependencies.

---

## Features

- **Automated timetable** — per-day schedules, 5 bell patterns, configurable duration
- **Holiday override** — mark any date to suppress all bells
- **Manual bell control** — ring or stop from the dashboard at any time
- **NTP time sync** — UDP NTP (pool.ntp.org) with HTTP fallback for Wokwi
- **Web dashboard** — single-page UI, nonce-based SHA-256 auth, CIDR IP ACL
- **WiFi auto-recovery** — non-blocking reconnect; AP fallback on missing SSID
- **Watchdog timer** — hardware reset on any hang; FakeWDT stub for Wokwi
- **Config backup** — download full config + schedule as JSON
- **Append-mode logs** — with 32 KiB rotation

---

## Hardware

| Pin    | Signal    | Connected to          |
|--------|-----------|-----------------------|
| GP15   | Bell out  | Buzzer positive       |
| GND    | GND       | Buzzer negative       |
| GP14   | Button in | Push-button (PULL_UP) |
| GP25   | LED       | Onboard LED           |

Wokwi simulation uses the included `diagram.json` directly.

---

## File Structure

```
main.py              — Entry point; boot sequence and 20 ms cooperative loop
config_manager.py    — config.json + wifi.json load/save with AES-128-CBC encryption
wifi_manager.py      — STA connect (WDT-safe), non-blocking reconnect, AP fallback
ntp_sync.py          — Blocking boot sync + non-blocking runtime tick() state machine
schedule_manager.py  — CRUD schedule, holiday management, append-mode event logging
bell_controller.py   — Non-blocking FSM bell driver; IRQ button handler
web_server.py        — Single-connection HTTP server; REST API; embedded SPA dashboard
config.json          — Runtime config (auto-created on first boot)
wifi.json            — WiFi credentials (auto-created on first boot)
schedule.json        — Weekly timetable + holidays
diagram.json         — Wokwi circuit definition
```

---

## Quick Start

### Wokwi Simulation

1. Open [wokwi.com](https://wokwi.com) and create a new project
2. Upload `diagram.json` and all `.py` files
3. Start the simulation — the dashboard URL appears in the serial console

### Physical Deployment (Pico W)

1. Flash MicroPython v1.24+ to the Pico W
2. Copy all `.py` files and JSON files to the root of the device
3. Edit `wifi.json` with your SSID and password
4. Power cycle — the device connects, syncs time, and starts the web server
5. Navigate to the IP shown in the serial output

### Default Credentials

| Field    | Value      |
|----------|------------|
| Username | `admin`    |
| Password | `admin123` |

**Change these immediately via the Config tab after first login.**

---

## API Reference

All endpoints except `/`, `/nonce`, and `/login` require the `X-Auth-Token` header.

| Method | Path                  | Description                        |
|--------|-----------------------|------------------------------------|
| GET    | `/`                   | Dashboard HTML                     |
| GET    | `/nonce`              | Fetch login nonce                  |
| POST   | `/login`              | Authenticate; returns token        |
| GET    | `/status`             | Current time, NTP status, next bell|
| GET    | `/schedule?day=X`     | Events for a given day             |
| POST   | `/schedule/add`       | Add a single event                 |
| POST   | `/schedule/edit`      | Edit an existing event             |
| POST   | `/schedule/delete`    | Delete an event                    |
| POST   | `/schedule/upload`    | Replace entire schedule from JSON  |
| POST   | `/schedule/holiday`   | Mark a date as holiday             |
| POST   | `/bell/ring`          | Trigger manual ring                |
| POST   | `/bell/stop`          | Stop current ring immediately      |
| GET    | `/config`             | Read config (no password)          |
| POST   | `/config/update`      | Update config + WiFi credentials   |
| GET    | `/config/backup`      | Download full backup JSON          |
| GET    | `/logs`               | Last 100 bell event log lines      |

---

## NTP Configuration

- Default: UDP NTP on `pool.ntp.org` (works on physical hardware)
- Wokwi blocks UDP port 123; set `"ntp_use_http": true` in `config.json` to use the worldtimeapi.org HTTP endpoint instead
- Timezone: IST (`timezone_offset: 19800` seconds = UTC+5:30)

---

## Security Notes

- Login uses a per-boot nonce + SHA-256 challenge — password never sent in plaintext
- `auth_pass` is stored AES-128-CBC encrypted on disk (device-unique key)
- IP allowlist: set `allowed_cidr` to restrict dashboard access (default: `0.0.0.0/0`)
- Any config change invalidates the current session token

---

## License

MIT License — free to use, modify, and deploy for educational purposes.

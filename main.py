# main.py  v1.0.0
# Production entry point for SBRR Mahajana Bell Automation System.
# Runs on Raspberry Pi Pico W (RP2350) with MicroPython v1.24+.
#
# Boot sequence:
#   1. Filesystem defaults written if absent
#   2. ConfigManager loads config.json + wifi.json
#   3. BellController initialised (IRQ registered)
#   4. Boot self-test (LED blink + 200 ms beep) — before WDT is armed
#   5. WDT armed (FakeWDT stub used in Wokwi where WDT is unavailable)
#   6. WiFi connect (wdt-safe polling, 15 s budget)
#   7. NTP sync (blocking, 3 s socket timeout)
#   8. ScheduleManager + WebServer started
#
# Main loop (20 ms cycle):
#   bell.tick() → scheduler.tick() → ntp.tick() → server.poll() → wifi.monitor()
#   Schedule match on minute boundary, NTP re-sync, WiFi auto-recovery,
#   WDT feed, amortised GC.

import utime
import gc
import ujson
from machine import Pin, WDT
from micropython import const

from config_manager   import ConfigManager
from wifi_manager     import WiFiManager
from ntp_sync         import NTPSync
from schedule_manager import ScheduleManager
from bell_controller  import BellController
from web_server       import WebServer

_VERSION              = '1.0.0'
_DAYS                 = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                         'Friday', 'Saturday', 'Sunday']
_LOOP_MS              = const(20)
_MAX_TRANSIENT_STREAK = const(20)
_GC_INTERVAL_CYCLES   = const(50)


# ── WDT stub — Wokwi does not support machine.WDT ────────────────────────────

class _FakeWDT:
    def feed(self):
        pass


# ── Ensure required JSON files exist with defaults ───────────────────────────

def _ensure_filesystem():
    defaults = {
        'config.json': {
            'bell_pin':            15,
            'led_pin':             25,
            'button_pin':          14,
            'ntp_host':            'pool.ntp.org',
            'ntp_interval_hours':  1,
            'timezone_offset':     19800,
            'auth_user':           'admin',
            'auth_pass':           'admin123',
            'web_port':            80,
            'watchdog_timeout_ms': 8388,
            'allowed_cidr':        '0.0.0.0/0',
            'ntp_use_http':        True,   # True = HTTP fallback for Wokwi; False = UDP NTP for physical hw
        },
        'wifi.json': {
            'ssid':        'Wokwi-GUEST',
            'password':    '',
            'ap_ssid':     'SBRRBell_AP',
            'ap_password': 'bellsystem',
        },
        'schedule.json': {
            'schedule': {},
            'holidays': [],
        },
    }
    for fname, default in defaults.items():
        try:
            with open(fname, 'r') as f:
                pass   # file exists — leave it unchanged
        except OSError:
            try:
                with open(fname, 'w') as f:
                    f.write(ujson.dumps(default))
                print(f'FS Created {fname}')
            except Exception as e:
                print(f'FS Could not create {fname}: {e}')


# ── Boot self-test (before WDT is armed) ─────────────────────────────────────

def _boot_selftest(led: Pin, bell: BellController):
    for _ in range(3):
        led.on()
        utime.sleep_ms(120)
        led.off()
        utime.sleep_ms(120)
    bell.test_ring()
    print('BOOT Self-test OK')


# ── Fatal halt — stop feeding WDT to force hardware reset ────────────────────

def _halt(reason: str):
    print(f'FATAL {reason} — halting WDT feed; hardware reset imminent.')
    while True:
        utime.sleep_ms(50)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'=' * 56}")
    print(f'  SBRR Mahajana Bell Automation System  v{_VERSION}')
    print(f"{'=' * 56}\n")
    t0 = utime.ticks_ms()

    led = Pin(25, Pin.OUT)
    led.on()

    _ensure_filesystem()

    cfg  = ConfigManager()
    bell = BellController(cfg)
    _boot_selftest(led, bell)

    # WDT — falls back to no-op stub in Wokwi where machine.WDT raises
    try:
        wdt = WDT(timeout=cfg.get('watchdog_timeout_ms', 8388))
    except Exception:
        print('BOOT WDT unavailable — using FakeWDT stub (Wokwi mode).')
        wdt = _FakeWDT()
    wdt.feed()

    wifi      = WiFiManager(cfg)
    connected = wifi.connect(wdt=wdt)   # feeds WDT every 500 ms — safe up to 15 s
    wdt.feed()

    ntp = NTPSync(cfg)
    if connected:
        ntp.sync(wdt=wdt)   # blocking, 3 s socket timeout; feeds WDT before+after
    wdt.feed()

    scheduler = ScheduleManager(cfg)
    server    = WebServer(cfg, scheduler, bell, ntp, wifi)
    if connected:
        server.start()

    led.off()
    print(f'MAIN Boot complete in {utime.ticks_diff(utime.ticks_ms(), t0)} ms\n')

    last_ntp_sync    = utime.time()
    last_hhmm        = ''
    ntp_interval_s   = cfg.get('ntp_interval_hours', 1) * 3600
    transient_streak = 0
    gc_counter       = 0

    while True:
        cycle_start = utime.ticks_ms()

        try:
            # ── Cooperative task dispatch ─────────────────────────────────
            bell.tick()
            scheduler.tick()
            ntp.tick()      # drives non-blocking NTP state machine
            if connected:
                server.poll()
            wifi.monitor()

            # ── Schedule match (fires once on each minute boundary) ───────
            now  = utime.localtime()
            hhmm = f'{now[3]:02d}:{now[4]:02d}'
            day  = _DAYS[now[6]] if now[6] < 7 else 'Sunday'

            if hhmm != last_hhmm:
                last_hhmm = hhmm
                event = scheduler.get_event(day, hhmm)
                if event:
                    print(f'SCHED {day} {hhmm} → {event["event_name"]}')
                    bell.ring(
                        event.get('bell_pattern',    'single_ring'),
                        event.get('duration_seconds', 3),
                    )
                    scheduler.log_event(day, hhmm, event['event_name'])

            # ── Periodic NTP re-sync (non-blocking: tick() drains it) ─────
            if connected and not ntp.is_pending():
                if (utime.time() - last_ntp_sync) >= ntp_interval_s:
                    ntp.request_sync()
                    last_ntp_sync = utime.time()

            # ── WiFi auto-recovery ────────────────────────────────────────
            if not wifi.is_connected():
                if connected:
                    print('WIFI Link dropped.')
                    connected = False
                    server.stop()
                if wifi.reconnect():
                    connected     = True
                    last_ntp_sync = 0   # force NTP re-sync after reconnect
                    ntp.request_sync()
                    server.start()
                    print('WIFI Reconnected.')
                    transient_streak = 0

        except OSError as e:
            transient_streak += 1
            print(f'WARN Transient OSError #{transient_streak}: {e}')
            if transient_streak >= _MAX_TRANSIENT_STREAK:
                _halt(f'OSError streak limit ({_MAX_TRANSIENT_STREAK}) reached')
        except Exception as e:
            _halt(f'{type(e).__name__}: {e}')

        # ── WDT fed exactly once per cycle ────────────────────────────────
        wdt.feed()

        # ── Amortised GC (every 50 cycles ≈ every 1 s) ───────────────────
        gc_counter += 1
        if gc_counter >= _GC_INTERVAL_CYCLES:
            gc.collect()
            gc_counter = 0

        # ── Pace loop to _LOOP_MS ─────────────────────────────────────────
        elapsed = utime.ticks_diff(utime.ticks_ms(), cycle_start)
        remain  = _LOOP_MS - elapsed
        if remain > 0:
            utime.sleep_ms(remain)


if __name__ == '__main__':
    main()

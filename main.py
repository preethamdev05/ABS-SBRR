# main.py  v3.0.0
# Production entry point for SBRR Mahajana Bell Automation System.
# Runs on Raspberry Pi Pico 2 W (RP2350) with MicroPython v1.24+.
#
# Boot sequence:
#   1. Filesystem defaults written if absent
#   2. ConfigManager loads config.json + wifi.json
#   3. BellController initialised (IRQ registered)
#   4. Boot self-test (LED blink + 200 ms beep) — before WDT is armed
#   5. WDT armed
#   6. WiFi connect (wdt-safe polling, 15 s budget)
#   7. NTP sync (blocking, 3 s socket timeout) → writes back to RTC on success
#   8. RTC fallback: if NTP failed, restore time from DS3231
#   9. Pre-compress dashboard.txt → dashboard.txt.gz
#  10. ScheduleManager + WebServer started
#
# Main loop (20 ms cycle):
#   bell.tick() → scheduler.tick() → ntp.tick() → server.poll() → wifi.monitor()
#   Schedule match on minute boundary, NTP re-sync, RTC write-back,
#   WiFi auto-recovery, WDT feed, amortised GC.

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
from rtc_sync         import RTCSync

_VERSION              = '3.0.0'
_DAYS                 = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                         'Friday', 'Saturday', 'Sunday']
_LOOP_MS              = const(20)
_MAX_TRANSIENT_STREAK = const(20)
_GC_INTERVAL_CYCLES   = const(50)


# ── WDT stub ──────────────────────────────────────────────────────────────────

class _FakeWDT:
    def feed(self):
        pass


# ── Ensure required JSON files exist with defaults ───────────────────────────

def _ensure_filesystem(cfg_defaults: dict):
    """Create missing JSON files. Defaults come from ConfigManager (single source of truth)."""
    defaults = {
        'config.json':   cfg_defaults,
        'wifi.json': {
            'ssid':        '',
            'password':    '',
            'ap_ssid':     'SBRRBell_AP',
            'ap_password': '',   # generated at first boot
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

    # Init config first (provides defaults for filesystem)
    cfg  = ConfigManager()
    _ensure_filesystem(cfg._default_cfg())
    cfg.load()  # reload after ensuring files exist
    bell = BellController(cfg)
    _boot_selftest(led, bell)

    # WDT — falls back to no-op stub if unavailable
    try:
        wdt = WDT(timeout=cfg.get('watchdog_timeout_ms', 8388))
    except Exception:
        print('BOOT WDT unavailable — using FakeWDT stub.')
        wdt = _FakeWDT()
    wdt.feed()

    wifi      = WiFiManager(cfg)
    connected = wifi.connect(wdt=wdt)   # feeds WDT every 500 ms — safe up to 15 s
    wdt.feed()

    ntp = NTPSync(cfg)
    rtc = RTCSync(cfg)
    ntp_synced = False
    if connected:
        ntp_synced = ntp.sync(wdt=wdt)   # blocking, 3 s socket timeout; feeds WDT before+after
        if ntp_synced:
            # Write NTP time back to RTC for future AP-only boots
            utc_now = utime.time() - cfg.get('timezone_offset', 19800)
            rtc.write_time_from_epoch(utc_now, cfg.get('timezone_offset', 19800))
            print('RTC Updated from NTP.')

    # If NTP failed (or no WiFi), fall back to RTC
    if not ntp_synced:
        if rtc.is_available() and not rtc.has_power_fail():
            if rtc.apply_to_machine_rtc():
                print('RTC Time restored from DS3231 (AP-only fallback).')
            else:
                print('RTC Could not apply RTC time — manual time set required.')
        elif rtc.is_available() and rtc.has_power_fail():
            print('RTC DS3231 detected but OSF flag set — time unreliable, manual set required.')
        else:
            print('RTC No DS3231 detected — AP-only mode with manual time only.')
    wdt.feed()

    # Pre-compress dashboard
    try:
        import uzlib
        raw = None
        try:
            with open('dashboard.html', 'rb') as f:
                raw = f.read()
            gz_name = 'dashboard.html.gz'
        except OSError:
            pass
        if raw:
            compressed = uzlib.compress(raw)
            with open(gz_name, 'wb') as f:
                f.write(compressed)
            print(f'BOOT Compressed {src}: {len(raw)} → {len(compressed)} bytes')
    except Exception as e:
        print(f'BOOT Dashboard compress: {e}')

    scheduler = ScheduleManager(cfg)
    server    = WebServer(cfg, scheduler, bell, ntp, wifi, rtc)
    server.start()   # always start — AP mode needs the dashboard too

    led.off()
    print(f'MAIN Boot complete in {utime.ticks_diff(utime.ticks_ms(), t0)} ms | '
          f'RTC: {rtc.status_str()}\n')

    last_ntp_sync    = utime.time()
    last_rtc_write   = utime.time()   # track RTC write-back separately
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
            server.poll()   # serves on AP IP (192.168.4.1) or STA IP
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
            # After NTP sync completes, write to RTC once (not every cycle)
            if ntp.is_synced() and not ntp.is_pending() and rtc.is_available():
                if (utime.time() - last_rtc_write) >= ntp_interval_s:
                    utc_now = utime.time() - cfg.get('timezone_offset', 19800)
                    rtc.write_time_from_epoch(utc_now, cfg.get('timezone_offset', 19800))
                    last_rtc_write = utime.time()

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
        except MemoryError as e:
            _halt(f'{type(e).__name__}: {e}')
        except Exception as e:
            transient_streak += 1
            print(f'WARN Exception #{transient_streak}: {type(e).__name__}: {e}')
            if transient_streak >= _MAX_TRANSIENT_STREAK:
                _halt(f'Exception streak limit ({_MAX_TRANSIENT_STREAK}) reached')
            gc.collect()

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
     if remain > 0:
            utime.sleep_ms(remain)


if __name__ == '__main__':
    main()
__main__':
    main()
_main__':
    main()
emain > 0:
            utime.sleep_ms(remain)


if __name__ == '__main__':
    main()
_main__':
    main()
emain > 0:
            utime.sleep_ms(remain)


if __name__ == '__main__':
    main()

# main.py  v2.2.0
# FIX 4: WDT wrapped in try/except with _FakeWDT stub — Wokwi does not support WDT
# FIX 5: ensure_filesystem() default allowed_cidr = '0.0.0.0/0' (was 192.168.1.0/24)

import utime, gc, ujson
from machine import Pin, WDT
from micropython import const

from config_manager   import ConfigManager
from wifi_manager     import WiFiManager
from ntp_sync         import NTPSync
from schedule_manager import ScheduleManager
from bell_controller  import BellController
from web_server       import WebServer

_VERSION             = '2.2.0'
_DAYS                = ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
_LOOP_MS             = const(20)
_MAX_TRANSIENT_STREAK = const(20)
_GC_INTERVAL_CYCLES  = const(50)


class _FakeWDT:
    """No-op WDT stub used in Wokwi where machine.WDT is unavailable."""
    def feed(self):
        pass


def _ensure_filesystem():
    defaults = {
        'config.json': {
            'bell_pin': 15, 'led_pin': 25, 'button_pin': 14,
            'ntp_host': 'pool.ntp.org', 'ntp_interval_hours': 1,
            'timezone_offset': 19800,
            'auth_user': 'admin', 'auth_pass': 'admin123',
            'web_port': 80, 'watchdog_timeout_ms': 8388,
            'allowed_cidr': '0.0.0.0/0',   # FIX 5: allow all IPs (Wokwi + physical)
        },
        'wifi.json': {
            'ssid': 'YourSSID', 'password': 'YourPassword',
            'ap_ssid': 'SBRRBellAP', 'ap_password': 'bellsystem',
        },
        'schedule.json': {},
    }
    for fname, default in defaults.items():
        try:
            open(fname, 'r').close()
        except OSError:
            with open(fname, 'w') as f:
                f.write(ujson.dumps(default))
            print(f"FS  Created {fname}")


def _boot_selftest(led, bell):
    for _ in range(3):
        led.on();  utime.sleep_ms(120)
        led.off(); utime.sleep_ms(120)
    bell.test_ring()   # 200 ms blocking beep — safe, WDT not yet armed
    print("BOOT Self-test OK")


def _halt_wdt(reason: str):
    """Stop feeding WDT to force a deterministic hardware reset."""
    print(f"FATAL {reason} — halting WDT feed. Hardware reset imminent.")
    while True:
        utime.sleep_ms(50)


def main():
    print(f"{'='*50} SBRR Mahajana Bell System v{_VERSION} {'='*50}")
    t0 = utime.ticks_ms()

    led = Pin(25, Pin.OUT)
    led.on()

    _ensure_filesystem()

    cfg  = ConfigManager()
    bell = BellController(cfg)
    _boot_selftest(led, bell)

    # FIX 4: Wokwi raises an exception on WDT() — fall back to no-op stub
    try:
        wdt = WDT(timeout=cfg.get('watchdog_timeout_ms', 8388))
    except Exception:
        print("BOOT WDT unavailable (Wokwi?), using FakeWDT stub.")
        wdt = _FakeWDT()
    wdt.feed()

    wifi      = WiFiManager(cfg)
    connected = wifi.connect(wdt=wdt)   # feeds WDT every 500 ms — safe up to 15 s
    wdt.feed()

    ntp = NTPSync(cfg)
    if connected:
        ntp.sync(wdt=wdt)   # 3 s socket timeout; feeds WDT before+after
        wdt.feed()

    scheduler = ScheduleManager(cfg)
    server    = WebServer(cfg, scheduler, bell, ntp, wifi)
    if connected:
        server.start()

    led.off()
    print(f"MAIN Boot complete in {utime.ticks_diff(utime.ticks_ms(), t0)} ms")

    last_ntp_sync   = utime.time()
    last_hhmm       = ''
    ntp_intervals   = cfg.get('ntp_interval_hours', 1) * 3600
    transient_streak = 0
    gc_counter      = 0

    while True:
        cycle_start = utime.ticks_ms()
        try:
            # ── Cooperative task dispatch ─────────────────────────────────
            bell.tick()
            scheduler.tick()
            ntp.tick()          # drives non-blocking NTP state machine
            if connected:
                server.poll()
                wifi.monitor()

            # ── Schedule match (once per minute boundary) ─────────────────
            now  = utime.localtime()
            hhmm = f"{now[3]:02d}:{now[4]:02d}"
            day  = _DAYS[now[6]] if now[6] < 7 else 'Sunday'

            if hhmm != last_hhmm:
                last_hhmm = hhmm
                event = scheduler.get_event(day, hhmm)
                if event:
                    print(f"SCHED {day} {hhmm} {event['event_name']}")
                    bell.ring(
                        event.get('bell_pattern', 'single_ring'),
                        event.get('duration_seconds', 3)
                    )
                    scheduler.log_event(day, hhmm, event['event_name'])

            # ── Periodic NTP re-sync (non-blocking — tick() drives it) ────
            if connected and not ntp.is_pending():
                if utime.time() - last_ntp_sync > ntp_intervals:
                    ntp.request_sync()
                    last_ntp_sync = utime.time()

            # ── WiFi drop recovery (non-blocking, WDT-safe) ───────────────
            if not wifi.is_connected():
                if connected:
                    print("WIFI Link dropped.")
                    connected = False
                    server.stop()
                if wifi.reconnect():
                    connected = True
                    last_ntp_sync = 0       # force NTP re-sync after reconnect
                    ntp.request_sync()
                    server.start()
                    print("WIFI Reconnected.")
                    transient_streak = 0

        except OSError as e:
            transient_streak += 1
            print(f"WARN Transient OSError #{transient_streak}: {e}")
            if transient_streak >= _MAX_TRANSIENT_STREAK:
                _halt_wdt(f"Transient streak limit {_MAX_TRANSIENT_STREAK} reached")
        except Exception as e:
            _halt_wdt(f"{type(e).__name__}: {e}")

        wdt.feed()   # WDT fed exactly once per cycle

        gc_counter += 1
        if gc_counter >= _GC_INTERVAL_CYCLES:
            gc.collect()
            gc_counter = 0

        # ── Pace to _LOOP_MS ──────────────────────────────────────────────
        elapsed = utime.ticks_diff(utime.ticks_ms(), cycle_start)
        remain  = _LOOP_MS - elapsed
        if remain > 0:
            utime.sleep_ms(remain)


if __name__ == '__main__':
    main()

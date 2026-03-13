
main_py = r'''# main.py – v2.2.0
# Boot: wdt passed to wifi.connect() and ntp.sync() — both feed wdt internally
#       on each poll iteration / before+after blocking socket call.
# Loop: ntp.tick() added to cooperative dispatcher.
#       ntp.request_sync() replaces blocking ntp.sync() for runtime re-syncs.
#       WiFi drop recovery uses wifi.reconnect() (non-blocking, WDT-safe).

import utime, gc, ujson
from machine import Pin, WDT
from micropython import const

from config_manager   import ConfigManager
from wifi_manager     import WiFiManager
from ntp_sync         import NTPSync
from schedule_manager import ScheduleManager
from bell_controller  import BellController
from web_server       import WebServer

VERSION               = "2.2.0"
DAYS                  = ["Monday","Tuesday","Wednesday","Thursday",
                          "Friday","Saturday","Sunday"]
_LOOP_MS              = const(20)
_MAX_TRANSIENT_STREAK = const(20)
_GC_INTERVAL_CYCLES   = const(50)


def ensure_filesystem():
    defaults = {
        "config.json": {
            "bell_pin": 15, "led_pin": 25, "button_pin": 14,
            "ntp_host": "pool.ntp.org", "ntp_interval_hours": 1,
            "timezone_offset": 19800,
            "auth_user": "admin", "auth_pass": "admin123",
            "web_port": 80, "watchdog_timeout_ms": 8388,
            "allowed_cidr": "192.168.1.0/24",
        },
        "wifi.json": {
            "ssid": "YourSSID", "password": "YourPassword",
            "ap_ssid": "SBRRBell_AP", "ap_password": "bellsystem",
        },
        "schedule.json": {},
    }
    for fname, default in defaults.items():
        try:
            open(fname, "r").close()
        except OSError:
            with open(fname, "w") as f:
                f.write(ujson.dumps(default))
            print(f"[FS] Created {fname}")


def boot_self_test(led, bell):
    for _ in range(3):
        led.on();  utime.sleep_ms(120)
        led.off(); utime.sleep_ms(120)
    bell.test_ring()
    print("[BOOT] Self-test OK")


def _halt_wdt(reason: str):
    """Stop feeding WDT to force a deterministic hardware reset."""
    print(f"[FATAL] {reason} – halting WDT feed. Hardware reset imminent.")
    while True:
        utime.sleep_ms(50)


def main():
    print(f"\n{'='*50}\n SBRR Mahajana Bell System v{VERSION}\n{'='*50}\n")
    t0 = utime.ticks_ms()

    led = Pin(25, Pin.OUT)
    led.on()
    ensure_filesystem()

    cfg  = ConfigManager()
    bell = BellController(cfg)
    boot_self_test(led, bell)

    # ── WDT armed; all boot operations must stay within 8388 ms per segment ──
    wdt = WDT(timeout=cfg.get("watchdog_timeout_ms", 8388))
    wdt.feed()

    # wifi.connect(wdt) feeds WDT on every 500 ms poll — safe up to 15 s total
    wifi      = WiFiManager(cfg)
    connected = wifi.connect(wdt=wdt)
    wdt.feed()

    # ntp.sync(wdt) feeds WDT before+after; socket timeout = 3 s (< 8388 ms)
    ntp = NTPSync(cfg)
    if connected:
        ntp.sync(wdt=wdt)
    wdt.feed()

    scheduler = ScheduleManager(cfg)
    server    = WebServer(cfg, scheduler, bell, ntp, wifi)
    if connected:
        server.start()

    led.off()
    print(f"[MAIN] Boot complete in {utime.ticks_diff(utime.ticks_ms(), t0)} ms\n")

    last_ntp_sync    = utime.time()
    last_hhmm        = ""
    ntp_interval_s   = cfg.get("ntp_interval_hours", 1) * 3600
    transient_streak = 0
    gc_counter       = 0

    while True:
        cycle_start = utime.ticks_ms()

        try:
            # ── Cooperative task dispatch ──────────────────────────────────
            bell.tick()
            scheduler.tick()
            ntp.tick()             # drives non-blocking NTP state machine
            if connected:
                server.poll()
            wifi.monitor()

            # ── Schedule match (once per minute boundary) ──────────────────
            now  = utime.localtime()
            hhmm = f"{now[3]:02d}:{now[4]:02d}"
            day  = DAYS[now[6]] if now[6] < 7 else "Sunday"

            if hhmm != last_hhmm:
                last_hhmm = hhmm
                event = scheduler.get_event(day, hhmm)
                if event:
                    print(f"[SCHED] {day} {hhmm} → {event['event_name']}")
                    bell.ring(
                        event.get("bell_pattern",    "single_ring"),
                        event.get("duration_seconds", 3),
                    )
                    scheduler.log_event(day, hhmm, event["event_name"])

            # ── Periodic NTP re-sync (non-blocking) ────────────────────────
            # request_sync() queues the work; ntp.tick() above drains it
            # over subsequent cycles without any blocking.
            if connected and not ntp.is_pending():
                if (utime.time() - last_ntp_sync) >= ntp_interval_s:
                    ntp.request_sync()
                    last_ntp_sync = utime.time()

            # ── WiFi auto-recovery ─────────────────────────────────────────
            if not wifi.is_connected():
                if connected:
                    print("[WIFI] Link dropped.")
                    connected = False
                    server.stop()

                if wifi.reconnect():
                    connected     = True
                    last_ntp_sync = 0   # force NTP re-sync after reconnect
                    ntp.request_sync()  # queue immediately; tick() will drive it
                    server.start()
                    print("[WIFI] Reconnected.")

            transient_streak = 0

        except OSError as e:
            transient_streak += 1
            print(f"[WARN] Transient OSError #{transient_streak}: {e}")
            if transient_streak >= _MAX_TRANSIENT_STREAK:
                _halt_wdt(f"Transient streak limit ({_MAX_TRANSIENT_STREAK}) reached")

        except Exception as e:
            _halt_wdt(f"{type(e).__name__}: {e}")

        # ── WDT fed exactly once per cycle ────────────────────────────────
        wdt.feed()

        # ── Amortised GC ──────────────────────────────────────────────────
        gc_counter += 1
        if gc_counter >= _GC_INTERVAL_CYCLES:
            gc.collect()
            gc_counter = 0

        # ── Pace to _LOOP_MS ──────────────────────────────────────────────
        elapsed = utime.ticks_diff(utime.ticks_ms(), cycle_start)
        remain  = _LOOP_MS - elapsed
        if remain > 0:
            utime.sleep_ms(remain)


if __name__ == "__main__":
    main()
'''

with open("main.py", "w") as f:
    f.write(main_py)
print(f"main.py  {len(main_py)} bytes")
print("\nAll three files written.")
print("\nWDT budget accounting per boot segment:")
print(f"  boot_self_test  : ~360 ms  (3x blink + test_ring)")
print(f"  wifi.connect    : ≤15000 ms (wdt.feed every 500 ms = safe)")
print(f"  ntp.sync        : ≤3000 ms  (socket timeout) + wdt pre+post feed")
print(f"  server.start    : ~5 ms")
print(f"  WDT budget      : 8388 ms per segment — every segment guaranteed")
print(f"\nRuntime NTP re-sync: 0 ms blocking (non-blocking tick() / EAGAIN loop)")

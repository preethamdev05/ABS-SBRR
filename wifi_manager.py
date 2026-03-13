# wifi_manager.py  v1.0.0
# Production: STA connect with WDT-safe polling, non-blocking reconnect,
# AP fallback on missing SSID or timeout. All WiFi credentials via get_wifi().

import network
import utime
from micropython import const

_CONNECT_TIMEOUT_MS = const(15000)
_POLL_MS            = const(500)


class WiFiManager:
    def __init__(self, cfg):
        self._cfg          = cfg
        self._sta          = network.WLAN(network.STA_IF)
        self._ap           = None
        self._reconnecting = False

    # ── Boot-time blocking connect ────────────────────────────────────────────

    def connect(self, wdt=None) -> bool:
        """
        Blocking connect with WDT keep-alive on every poll cycle.
        Safe up to 15 s total; WDT budget is 8388 ms per feed interval.
        Falls back to AP mode on empty SSID or timeout.
        """
        ssid = self._cfg.get_wifi('ssid', '')
        pw   = self._cfg.get_wifi('password', '')

        if not ssid:
            print('WIFI No SSID configured — entering AP mode.')
            self._start_ap()
            return False

        self._sta.active(True)
        if self._sta.isconnected():
            print(f'WIFI Already connected: {self._sta.ifconfig()[0]}')
            return True

        print(f'WIFI Connecting to "{ssid}"…')
        try:
            self._sta.connect(ssid, pw)
        except OSError as e:
            print(f'WIFI connect() error: {e} — AP fallback.')
            self._start_ap()
            return False

        deadline = utime.ticks_add(utime.ticks_ms(), _CONNECT_TIMEOUT_MS)
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            if wdt:
                wdt.feed()
            if self._sta.isconnected():
                print(f'WIFI Connected — IP: {self._sta.ifconfig()[0]}')
                self._reconnecting = False
                return True
            utime.sleep_ms(_POLL_MS)

        print('WIFI Connect timeout — AP fallback.')
        self._start_ap()
        return False

    # ── Non-blocking reconnect (called inside armed WDT loop) ────────────────

    def reconnect(self) -> bool:
        """
        Single non-blocking step; returns True once link is established.
        Must be called on every loop cycle after a drop.
        Main loop is responsible for WDT feeds — no blocking here.
        """
        ssid = self._cfg.get_wifi('ssid', '')
        pw   = self._cfg.get_wifi('password', '')

        if not ssid:
            return False

        self._sta.active(True)
        if self._sta.isconnected():
            self._reconnecting = False
            return True

        if not self._reconnecting:
            print('WIFI Initiating non-blocking reconnect…')
            try:
                self._sta.connect(ssid, pw)
            except OSError:
                pass
            self._reconnecting = True

        return False

    # ── Cooperative monitor hook ──────────────────────────────────────────────

    def monitor(self):
        """No-op hook — reserved for future RSSI logging or AP watchdog."""
        pass

    # ── AP fallback ───────────────────────────────────────────────────────────

    def _start_ap(self):
        ap_ssid = self._cfg.get_wifi('ap_ssid',     'SBRRBell_AP')
        ap_pass = self._cfg.get_wifi('ap_password', 'bellsystem')
        self._ap = network.WLAN(network.AP_IF)
        self._ap.active(True)
        # ssid= parameter name; security=3 (WPA2-PSK) — compatible with Wokwi v1.24+
        self._ap.config(ssid=ap_ssid, password=ap_pass, security=3)
        utime.sleep_ms(500)
        print(f'WIFI AP active — SSID: {ap_ssid}  IP: 192.168.4.1')

    # ── Accessors ─────────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._sta.isconnected()

    def get_ip(self) -> str:
        if self._sta.isconnected():
            return self._sta.ifconfig()[0]
        if self._ap and self._ap.active():
            return '192.168.4.1'
        return '0.0.0.0'

    def disconnect(self):
        if self._sta.isconnected():
            self._sta.disconnect()
        self._reconnecting = False

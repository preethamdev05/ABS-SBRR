# wifi_manager.py  v2.2.0
# FIX 1: All WiFi credentials read via cfg.get_wifi() not cfg.get()
# FIX 2: AP config uses ssid= (not essid=) and authmode=3

import network, utime
from micropython import const

_CONNECT_TIMEOUT_MS = const(15000)
_POLL_MS = const(500)


class WiFiManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sta = network.WLAN(network.STA_IF)
        self.ap = None
        self.reconnecting = False

    def connect(self, wdt=None) -> bool:
        """Boot-time blocking connect. Feeds wdt every _POLL_MS — safe up to 15 s total."""
        ssid = self.cfg.get_wifi('ssid', '')
        pw   = self.cfg.get_wifi('password', '')
        if not ssid:
            print("WIFI No SSID configured, entering AP mode.")
            self._start_ap()
            return False

        self.sta.active(True)
        if self.sta.isconnected():
            print(f"WIFI Already connected {self.sta.ifconfig()[0]}")
            return True

        print(f"WIFI Connecting to {ssid}")
        self.sta.connect(ssid, pw)
        deadline = utime.ticks_add(utime.ticks_ms(), _CONNECT_TIMEOUT_MS)
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            if wdt:
                wdt.feed()
            if self.sta.isconnected():
                print(f"WIFI Connected IP {self.sta.ifconfig()[0]}")
                self.reconnecting = False
                return True
            utime.sleep_ms(_POLL_MS)

        print("WIFI Connect timeout. AP fallback.")
        self._start_ap()
        return False

    def reconnect(self) -> bool:
        """Single non-blocking step. Returns True once link is re-established.
        Call every loop cycle after a link drop. Main loop owns WDT feeds."""
        ssid = self.cfg.get_wifi('ssid', '')
        pw   = self.cfg.get_wifi('password', '')
        if not ssid:
            return False
        self.sta.active(True)
        if self.sta.isconnected():
            self.reconnecting = False
            return True
        if not self.reconnecting:
            print("WIFI Initiating non-blocking reconnect")
            try:
                self.sta.connect(ssid, pw)
            except OSError:
                pass
            self.reconnecting = True
        return False  # still waiting — main loop retries next cycle

    def monitor(self):
        """Cooperative monitor hook (future RSSI logging / AP fallback logic)."""
        pass

    def _start_ap(self):
        """AP fallback. utime.sleep_ms(500) is safe — called before WDT is armed."""
        ap_ssid = self.cfg.get_wifi('ap_ssid', 'SBRRBellAP')
        ap_pass = self.cfg.get_wifi('ap_password', 'bellsystem')
        self.ap = network.WLAN(network.AP_IF)
        self.ap.active(True)
        # Use ssid= (not essid=) — works on both physical firmware and Wokwi v1.24+
        self.ap.config(ssid=ap_ssid, password=ap_pass, security=3)
        utime.sleep_ms(500)
        print(f"WIFI AP active  SSID={ap_ssid}  IP=192.168.4.1")

    # ── Accessors ────────────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        return self.sta.isconnected()

    def get_ip(self) -> str:
        if self.sta.isconnected():
            return self.sta.ifconfig()[0]
        if self.ap and self.ap.active():
            return '192.168.4.1'
        return '0.0.0.0'

    def disconnect(self):
        if self.sta.isconnected():
            self.sta.disconnect()
        self.reconnecting = False

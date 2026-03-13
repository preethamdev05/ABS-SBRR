
ntp_sync = r'''# ntp_sync.py – v2.2.0
# Boot:    sync(wdt) – blocking with WDT keep-alive; socket timeout reduced
#          to 3 s to stay well within the 8388 ms WDT budget.
# Runtime: request_sync() + tick() – fully non-blocking state machine.
#          Each tick() call returns in < 1 ms on the EAGAIN path.
#          DNS result cached after first resolution; subsequent syncs skip
#          getaddrinfo entirely, eliminating the blocking DNS window.

import usocket, ustruct, utime, machine
from micropython import const

NTP_DELTA        = const(2208988800)   # seconds between 1900-01-01 and 1970-01-01
_NTP_PORT        = const(123)
_NTP_PACKET      = b"\x1b" + 47 * b"\x00"
_RECV_TIMEOUT_MS = const(4000)         # non-blocking recv polling window

_IDLE    = const(0)
_SENDING = const(1)
_WAITING = const(2)


class NTPSync:
    def __init__(self, cfg):
        self._cfg     = cfg
        self._synced  = False
        self._last_t  = 0
        self._state   = _IDLE
        self._sock    = None
        self._send_ts = 0
        self._tz      = 0
        self._addr    = None   # cached getaddrinfo result; survives reboots of NTP

    # ── Boot-time blocking sync ────────────────────────────────────────────────
    # Safe to call only while WDT was just fed.
    # Socket timeout = 3 s; leaves 5.3 s headroom in the 8388 ms WDT budget.
    def sync(self, wdt=None) -> bool:
        if wdt:
            wdt.feed()   # consume full WDT window from this point forward
        host      = self._cfg.get("ntp_host",        "pool.ntp.org")
        tz_offset = self._cfg.get("timezone_offset", 19800)
        s = None
        try:
            if self._addr is None:
                self._addr = usocket.getaddrinfo(host, _NTP_PORT)[0][-1]
            s = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
            s.settimeout(3)        # hard cap at 3 s
            s.sendto(_NTP_PACKET, self._addr)
            data, _ = s.recvfrom(48)
            self._apply(data, tz_offset)
            return True
        except Exception as e:
            print(f"[NTP] Sync failed: {e}")
            self._addr = None      # force fresh DNS lookup next attempt
            return False
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
            if wdt:
                wdt.feed()         # re-arm WDT immediately on return

    # ── Runtime non-blocking interface ────────────────────────────────────────
    def request_sync(self):
        """Schedule a non-blocking sync. tick() drives it to completion."""
        if self._state == _IDLE:
            self._state = _SENDING

    def tick(self):
        """
        Called every 20 ms loop cycle.
        SENDING: resolves DNS (cached = ~0 ms) and sends UDP packet.
        WAITING: polls recv() with setblocking(False); EAGAIN returns instantly.
                 Times out after _RECV_TIMEOUT_MS and resets to IDLE.
        Never sleeps; never feeds the WDT (main loop owns that).
        """
        if self._state == _IDLE:
            return

        elif self._state == _SENDING:
            host      = self._cfg.get("ntp_host",        "pool.ntp.org")
            tz_offset = self._cfg.get("timezone_offset", 19800)
            try:
                if self._addr is None:
                    # Blocking DNS – cached after first call; typically < 200 ms
                    self._addr = usocket.getaddrinfo(host, _NTP_PORT)[0][-1]
                self._sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
                self._sock.setblocking(False)
                self._sock.sendto(_NTP_PACKET, self._addr)
                self._send_ts = utime.ticks_ms()
                self._tz      = tz_offset
                self._state   = _WAITING
            except Exception as e:
                print(f"[NTP] Send error: {e}")
                self._addr = None    # invalidate cache; re-resolve next attempt
                self._close_sock()
                self._state = _IDLE

        elif self._state == _WAITING:
            elapsed = utime.ticks_diff(utime.ticks_ms(), self._send_ts)
            if elapsed > _RECV_TIMEOUT_MS:
                print(f"[NTP] Recv timeout after {elapsed} ms.")
                self._close_sock()
                self._state = _IDLE
                return
            try:
                data, _ = self._sock.recvfrom(48)
                if len(data) >= 44:
                    self._apply(data, self._tz)
                else:
                    print("[NTP] Malformed packet.")
                self._close_sock()
                self._state = _IDLE
            except OSError:
                pass   # EAGAIN: response not yet arrived; return, retry next tick

    # ── RTC application ───────────────────────────────────────────────────────
    def _apply(self, data: bytes, tz_offset: int):
        ntp_time  = ustruct.unpack("!I", data[40:44])[0]
        unix_time = ntp_time - NTP_DELTA + tz_offset
        t         = utime.localtime(unix_time)
        machine.RTC().datetime((t[0], t[1], t[2], t[6] + 1, t[3], t[4], t[5], 0))
        self._synced = True
        self._last_t = utime.time()
        print(f"[NTP] Synced: {t[0]}-{t[1]:02d}-{t[2]:02d} "
              f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d} IST")

    def _close_sock(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── Accessors ─────────────────────────────────────────────────────────────
    def is_synced(self) -> bool:
        return self._synced

    def is_pending(self) -> bool:
        return self._state != _IDLE

    def last_sync_str(self) -> str:
        if not self._synced:
            return "Never"
        t = utime.localtime(self._last_t)
        return f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}"
'''

with open("ntp_sync.py", "w") as f:
    f.write(ntp_sync)
print(f"ntp_sync.py  {len(ntp_sync)} bytes")

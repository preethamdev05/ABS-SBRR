# ntp_sync.py  v2.3.0
# Replaced UDP NTP with HTTP/TCP GET to worldtimeapi.org — Wokwi-compatible.
# Wokwi blocks UDP (port 123) but permits TCP. Physical hardware works identically.
# Extracts "unixtime" integer from JSON, adds timezone_offset, sets machine.RTC().

import usocket, ujson, utime, machine
from micropython import const

_HOST    = 'worldtimeapi.org'
_PORT    = const(80)
_PATH    = '/api/timezone/Asia/Kolkata'
_TIMEOUT = const(5)        # socket timeout seconds — well within 8388 ms WDT budget

# HTTP/1.0 forces server to close connection after body — avoids chunked encoding
_REQUEST = (
    'GET /api/timezone/Asia/Kolkata HTTP/1.0\r\n'
    'Host: worldtimeapi.org\r\n'
    'Accept: application/json\r\n'
    'Connection: close\r\n'
    '\r\n'
)

_IDLE    = const(0)
_SENDING = const(1)   # tick() executes full round-trip in this state


class NTPSync:
    def __init__(self, cfg):
        self.cfg    = cfg
        self.synced = False
        self.last_t = 0
        self.state  = _IDLE
        self._addr  = None   # cached DNS result — avoids repeated lookups

    # ── Internal: DNS resolve (cached) ───────────────────────────────────────
    def _resolve(self):
        if self._addr is None:
            info = usocket.getaddrinfo(_HOST, _PORT)
            if not info:
                raise OSError('DNS failed for worldtimeapi.org')
            self._addr = info[0][-1]
        return self._addr

    # ── Internal: full blocking HTTP GET → returns unixtime int ──────────────
    def _fetch_unixtime(self) -> int:
        addr = self._resolve()
        s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
        s.settimeout(_TIMEOUT)
        buf = bytearray()
        try:
            s.connect(addr)
            s.send(_REQUEST.encode())
            while True:
                try:
                    chunk = s.recv(256)
                except OSError:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
        finally:
            try:
                s.close()
            except Exception:
                pass

        # Split HTTP headers from body on \r\n\r\n
        raw   = bytes(buf)
        delim = raw.find(b'\r\n\r\n')
        if delim < 0:
            raise ValueError('HTTP response missing header terminator')
        body = raw[delim + 4:]

        # Check HTTP status line (first line of headers)
        status_line = raw[:raw.find(b'\r\n')].decode('utf-8', 'ignore')
        if ' 200 ' not in status_line:
            raise OSError(f'HTTP error: {status_line}')

        # Parse JSON body and extract unixtime
        data = ujson.loads(body.decode('utf-8', 'ignore'))
        if 'unixtime' not in data:
            raise ValueError('JSON missing "unixtime" key')
        return int(data['unixtime'])

    # ── Boot-time blocking sync ───────────────────────────────────────────────
    def sync(self, wdt=None) -> bool:
        """Called once at boot. Feeds WDT before and after.
        _FakeWDT.feed() is a no-op in Wokwi — safe either way."""
        if wdt:
            wdt.feed()
        try:
            unix_time = self._fetch_unixtime()
            tz_offset = self.cfg.get('timezone_offset', 19800)
            self.apply(unix_time, tz_offset)
            return True
        except Exception as e:
            print(f'NTP Sync failed: {e}')
            self._addr = None   # force fresh DNS next attempt
            return False
        finally:
            if wdt:
                wdt.feed()

    # ── Runtime non-blocking interface ───────────────────────────────────────
    def request_sync(self):
        """Queue a sync. tick() executes it on the next loop cycle."""
        if self.state == _IDLE:
            self.state = _SENDING

    def tick(self):
        """Called every 20 ms loop cycle.
        In SENDING state, performs the full HTTP round-trip (typically <500 ms,
        max _TIMEOUT=5 s). Blocks the loop only during active sync.
        On Wokwi: _FakeWDT is used — no WDT risk.
        On physical hardware: 5 s << 8388 ms WDT budget — safe."""
        if self.state != _SENDING:
            return
        try:
            unix_time = self._fetch_unixtime()
            tz_offset = self.cfg.get('timezone_offset', 19800)
            self.apply(unix_time, tz_offset)
        except Exception as e:
            print(f'NTP tick failed: {e}')
            self._addr = None   # invalidate cache — re-resolve next attempt
        finally:
            self.state = _IDLE  # always return to IDLE regardless of success/failure

    # ── Apply unix_time to hardware RTC ──────────────────────────────────────
    def apply(self, unix_time: int, tz_offset: int):
        """unix_time is UTC epoch from worldtimeapi.
        Add tz_offset (IST = 19800 s) to get local wall-clock time for RTC.
        machine.RTC().datetime() weekday: 1=Mon…7=Sun;
        utime.localtime()[6]:            0=Mon…6=Sun  → add 1."""
        local = unix_time + tz_offset
        t = utime.localtime(local)
        machine.RTC().datetime((t[0], t[1], t[2], t[6] + 1, t[3], t[4], t[5], 0))
        self.synced = True
        self.last_t = utime.time()
        print(f'NTP Synced {t[0]}-{t[1]:02d}-{t[2]:02d} '
              f'{t[3]:02d}:{t[4]:02d}:{t[5]:02d} IST')

    # ── Accessors ─────────────────────────────────────────────────────────────
    def is_synced(self) -> bool:
        return self.synced

    def is_pending(self) -> bool:
        return self.state != _IDLE

    def last_sync_str(self) -> str:
        if not self.synced:
            return 'Never'
        t = utime.localtime(self.last_t)
        return f'{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}'

# ntp_sync.py  v2.0.0
# Two-mode time sync: UDP NTP (primary) and HTTP worldtimeapi.org (fallback).
# Set "ntp_use_http": true in config.json to force HTTP mode.
#
# Boot path: sync(wdt)
#   - WDT fed at entry, after DNS, after HTTP connect, after recv — every
#     segment is well under the 8388 ms WDT budget.
#   - HTTP socket timeout: 4 s.  UDP socket timeout: 3 s.
#
# Runtime path: request_sync() + tick()
#   - Fully non-blocking UDP state machine.
#   - HTTP mode: single blocking tick (< 4 s, safe — WDT fed by main loop
#     on next cycle; 8388 ms >> typical HTTP round-trip of 500–1500 ms).

import usocket
import ustruct
import utime
import ujson
import machine
from micropython import const

_NTP_DELTA       = const(2208988800)   # epoch offset: 1900-01-01 → 1970-01-01
_NTP_PORT        = const(123)
_NTP_PACKET      = b'\x1b' + b'\x00' * 47
_RECV_TIMEOUT_MS = const(4000)

_HTTP_HOST = 'worldtimeapi.org'
_HTTP_PORT = const(80)


def _build_http_req(timezone_name: str) -> bytes:
    path = f'/api/timezone/{timezone_name}'.encode()
    return (
        b'GET ' + path + b' HTTP/1.0\r\n'
        b'Host: worldtimeapi.org\r\n'
        b'Accept: application/json\r\n'
        b'Connection: close\r\n'
        b'\r\n'
    )

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
        self._addr    = None   # cached DNS result

    # ── DNS resolution (cached) ───────────────────────────────────────────────

    def _resolve(self, host, port):
        """Resolve host:port and return address tuple. Result cached in self._addr."""
        if self._addr is None:
            info = usocket.getaddrinfo(host, port)
            if not info:
                raise OSError(f'DNS failed: {host}')
            self._addr = info[0][-1]
        return self._addr

    # ── HTTP fetch — WDT-safe segmented version ───────────────────────────────

    def _http_fetch_unixtime(self, wdt=None) -> int:
        """
        Fetches unixtime from worldtimeapi.org.
        wdt.feed() is called after DNS, after connect, and after recv so that
        no single segment exceeds the 8388 ms WDT window.
        """
        addr = self._resolve(_HTTP_HOST, _HTTP_PORT)
        if wdt:
            wdt.feed()   # fed after DNS (DNS can take up to ~2 s)

        timezone_name = self._cfg.get('timezone_name', 'Asia/Kolkata')
        http_req = _build_http_req(timezone_name)

        s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
        s.settimeout(4)
        buf = bytearray()
        try:
            s.connect(addr)
            if wdt:
                wdt.feed()   # fed after TCP connect
            s.send(http_req)
            while True:
                try:
                    chunk = s.recv(512)
                except OSError:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
                if wdt:
                    wdt.feed()   # fed during long recv
        finally:
            try:
                s.close()
            except Exception:
                pass

        if wdt:
            wdt.feed()   # fed after full recv

        raw   = bytes(buf)
        delim = raw.find(b'\r\n\r\n')
        if delim < 0:
            raise ValueError('HTTP response malformed — no header terminator')

        status_end = raw.find(b'\r\n')
        status     = raw[:status_end].decode('utf-8', 'ignore') if status_end >= 0 else ''
        if ' 200 ' not in status:
            raise OSError(f'HTTP {status.split(" ", 2)[1] if " " in status else "error"}')

        body = raw[delim + 4:]
        if not body:
            raise ValueError('HTTP response has empty body')

        data = ujson.loads(body.decode('utf-8', 'ignore'))
        if 'unixtime' not in data:
            raise ValueError('JSON missing "unixtime" key')
        return int(data['unixtime'])

    # ── Boot-time blocking sync ───────────────────────────────────────────────

    def sync(self, wdt=None) -> bool:
        """
        Called once at boot after WiFi connects.
        Passes wdt into _http_fetch_unixtime() so it feeds on every
        segment — safe against the 8388 ms WDT even on slow networks.
        """
        if wdt:
            wdt.feed()

        tz_offset = self._cfg.get('timezone_offset', 19800)
        use_http  = self._cfg.get('ntp_use_http', False)
        s         = None
        try:
            if use_http:
                unix_time = self._http_fetch_unixtime(wdt=wdt)
            else:
                addr = self._resolve(self._cfg.get('ntp_host', 'pool.ntp.org'), _NTP_PORT)
                if wdt:
                    wdt.feed()
                s = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
                s.settimeout(3)
                s.sendto(_NTP_PACKET, addr)
                data, _ = s.recvfrom(48)
                if len(data) < 44:
                    raise ValueError('NTP packet too short')
                ntp_t     = ustruct.unpack('!I', data[40:44])[0]
                unix_time = ntp_t - _NTP_DELTA
            self._apply(unix_time, tz_offset)
            return True
        except Exception as e:
            print(f'NTP sync failed: {e}')
            self._addr = None
            return False
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            if wdt:
                wdt.feed()

    # ── Runtime non-blocking interface ───────────────────────────────────────

    def request_sync(self):
        """Queue a background sync. tick() drains it."""
        if self._state == _IDLE:
            self._state = _SENDING

    def tick(self):
        """
        Called every 20 ms loop cycle.
        HTTP mode: executes the full round-trip in a single tick.
          Typical latency 500–1500 ms — safely within 8388 ms WDT
          budget since the main loop feeds WDT immediately after tick().
        UDP mode: non-blocking SENDING → WAITING → IDLE state machine;
          each tick returns in < 1 ms on the EAGAIN path.
        """
        if self._state == _IDLE:
            return

        use_http  = self._cfg.get('ntp_use_http', False)
        tz_offset = self._cfg.get('timezone_offset', 19800)

        if self._state == _SENDING:
            try:
                if use_http:
                    unix_time = self._http_fetch_unixtime(wdt=None)
                    self._apply(unix_time, tz_offset)
                else:
                    addr       = self._resolve(
                        self._cfg.get('ntp_host', 'pool.ntp.org'), _NTP_PORT
                    )
                    self._sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
                    self._sock.setblocking(False)
                    self._sock.sendto(_NTP_PACKET, addr)
                    self._send_ts = utime.ticks_ms()
                    self._tz      = tz_offset
                    self._state   = _WAITING
                    return
            except Exception as e:
                print(f'NTP tick error: {e}')
                self._addr = None
                self._close_sock()
            self._state = _IDLE

        elif self._state == _WAITING:
            elapsed = utime.ticks_diff(utime.ticks_ms(), self._send_ts)
            if elapsed > _RECV_TIMEOUT_MS:
                print(f'NTP recv timeout after {elapsed} ms')
                self._close_sock()
                self._state = _IDLE
                return
            try:
                data, _ = self._sock.recvfrom(48)
                if len(data) >= 44:
                    ntp_t     = ustruct.unpack('!I', data[40:44])[0]
                    unix_time = ntp_t - _NTP_DELTA
                    self._apply(unix_time, self._tz)
                else:
                    print('NTP malformed packet')
            except OSError:
                return   # EAGAIN — response not yet arrived
            finally:
                self._close_sock()
            self._state = _IDLE

    # ── Apply unix timestamp to RTC ───────────────────────────────────────────

    def _apply(self, unix_time: int, tz_offset: int):
        local = unix_time + tz_offset
        t     = utime.localtime(local)
        # machine.RTC().datetime() weekday: 1=Mon…7=Sun
        # utime.localtime()[6]:             0=Mon…6=Sun → +1
        machine.RTC().datetime((t[0], t[1], t[2], t[6] + 1, t[3], t[4], t[5], 0))
        self._synced = True
        self._last_t = utime.time()
        print(f'NTP synced {t[0]}-{t[1]:02d}-{t[2]:02d} '
              f'{t[3]:02d}:{t[4]:02d}:{t[5]:02d} IST')

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
            return 'Never'
        t = utime.localtime(self._last_t)
        return f'{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}'

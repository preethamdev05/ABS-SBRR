# web_server.py  v3.0.0
# Production: Non-blocking single-connection HTTP server on RP2350/Pico W.
# Auth: nonce-based SHA-256 challenge with per-login nonce rotation.
# Security: rate limiting, token TTL, CIDR ACL, password-stripped backups.
# UI:   Compressed dashboard.txt.gz served with Content-Encoding: gzip.

import usocket
import ujson
import utime
import gc
import uos
import uhashlib
import ubinascii
from micropython import const

_VERSION = '3.0.0'
_DAYS    = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_MAX_BUF = const(2048)
_CHUNK   = const(256)
_TIMEOUT = 3

_HTTP_REASON = {
    200: 'OK',
    204: 'No Content',
    400: 'Bad Request',
    401: 'Unauthorized',
    403: 'Forbidden',
    404: 'Not Found',
    405: 'Method Not Allowed',
    429: 'Too Many Requests',
}


# ── Crypto helpers ────────────────────────────────────────────────────────────

def _sha256hex(data: str) -> str:
    h = uhashlib.sha256(data.encode())
    return ubinascii.hexlify(h.digest()).decode()


def _boot_nonce() -> str:
    try:
        return ubinascii.hexlify(uos.urandom(16)).decode()
    except Exception:
        import machine
        raw = machine.unique_id() * 2
        return ubinascii.hexlify(raw).decode()


# ── CIDR subnet validation ────────────────────────────────────────────────────

def _ip_to_int(ip: str) -> int:
    parts = ip.split('.')
    if len(parts) != 4:
        raise ValueError(f'Invalid IP: {ip}')
    n = 0
    for p in parts:
        v = int(p)
        if v < 0 or v > 255:
            raise ValueError(f'Octet out of range: {v}')
        n = (n << 8) | v
    return n


def _cidr_allows(ip: str, cidr: str) -> bool:
    try:
        net, bits_str = cidr.split('/')
        bits = int(bits_str)
        if bits < 0 or bits > 32:
            return False
        mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
        return (_ip_to_int(ip) & mask) == (_ip_to_int(net) & mask)
    except Exception:
        return False


# ── Compressed dashboard loader ───────────────────────────────────────────────

_DASHBOARD_GZ = None  # populated by _load_dashboard()


def _load_dashboard():
    """Load pre-compressed .gz or fall back to compressing source file.
    Tries dashboard.html first (local), then dashboard.txt (Wokwi)."""
    global _DASHBOARD_GZ
    # 1. Try pre-compressed file (.html.gz or .txt.gz)
    for gz in ('dashboard.html.gz', 'dashboard.txt.gz'):
        try:
            with open(gz, 'rb') as f:
                _DASHBOARD_GZ = f.read()
            print(f'WEB Loaded {gz} ({len(_DASHBOARD_GZ)} bytes)')
            return
        except OSError:
            pass

    # 2. Fall back to reading and compressing source file
    for src in ('dashboard.html', 'dashboard.txt'):
        try:
            import uzlib
            with open(src, 'rb') as f:
                raw = f.read()
            _DASHBOARD_GZ = uzlib.compress(raw)
            print(f'WEB Compressed {src}: {len(raw)} → {len(_DASHBOARD_GZ)} bytes')
            return
        except OSError:
            pass

    print('WEB No dashboard file found — dashboard will not be served.')


class WebServer:
    def __init__(self, cfg, scheduler, bell, ntp, wifi, rtc=None):
        self._cfg     = cfg
        self._sched   = scheduler
        self._bell    = bell
        self._ntp     = ntp
        self._wifi    = wifi
        self._rtc     = rtc
        self._sock    = None
        self._running = False
        self._nonce   = _boot_nonce()
        self._token   = None
        self._token_ts = 0          # timestamp when token was issued
        # Rate limiting
        self._login_attempts = 0    # failed attempt counter
        self._lockout_until  = 0    # unix timestamp lockout expires
        self._boot_ticks     = utime.ticks_ms()   # ticks-based for reliable uptime

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        # Load dashboard into memory before binding port
        _load_dashboard()
        port = self._cfg.get('web_port', 80)
        try:
            self._sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
            self._sock.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
            self._sock.bind(('0.0.0.0', port))
            self._sock.listen(2)
            self._sock.setblocking(False)
            self._running = True
            print(f'WEB Dashboard at http://{self._wifi.get_ip()}:{port}')
        except Exception as e:
            print(f'WEB Start failed: {e}')
            self._running = False

    def stop(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._running = False

    # ── IP ACL ────────────────────────────────────────────────────────────────

    def _ip_allowed(self, ip: str) -> bool:
        cidr = self._cfg.get('allowed_cidr', '0.0.0.0/0')
        return _cidr_allows(ip, cidr)

    # ── Cooperative poll — called every loop cycle ────────────────────────────

    def poll(self):
        if not self._sock:
            return
        try:
            conn, addr = self._sock.accept()
        except OSError:
            return   # EAGAIN — no pending connection

        remote_ip = addr[0]
        if not self._ip_allowed(remote_ip):
            print(f'WEB Blocked {remote_ip} (outside CIDR)')
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            conn.settimeout(_TIMEOUT)
            buf        = bytearray()
            header_end = -1

            while len(buf) < _MAX_BUF:
                try:
                    chunk = conn.recv(_CHUNK)
                except OSError:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
                idx = bytes(buf).find(b'\r\n\r\n')
                if idx >= 0:
                    header_end = idx
                    break

            if header_end < 0:
                conn.send(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
                return

            try:
                header_str = bytes(buf[:header_end]).decode('utf-8', 'ignore')
            except Exception:
                return

            lines = header_str.split('\r\n')
            if not lines:
                return
            parts = lines[0].split(' ')
            if len(parts) < 2:
                return
            method   = parts[0]
            fullpath = parts[1]

            if method not in ('GET', 'POST', 'PUT', 'HEAD', 'OPTIONS'):
                conn.send(b'HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n')
                return

            # Handle OPTIONS preflight (CORS)
            if method == 'OPTIONS':
                header = (
                    'HTTP/1.1 204 No Content\r\n'
                    'Access-Control-Allow-Origin: *\r\n'
                    'Access-Control-Allow-Methods: GET, POST, PUT, OPTIONS\r\n'
                    'Access-Control-Allow-Headers: Content-Type, X-Auth-Token\r\n'
                    'Access-Control-Max-Age: 86400\r\n'
                    'Content-Length: 0\r\n\r\n'
                ).encode()
                conn.send(header)
                return

            # Handle favicon
            if method == 'GET' and fullpath == '/favicon.ico':
                conn.send(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n')
                return

            # Read body up to Content-Length limit
            body_bytes = bytes(buf[header_end + 4:])
            clen       = 0
            for line in lines[1:]:
                if line.lower().startswith('content-length'):
                    try:
                        clen = int(line.split(':', 1)[1].strip())
                    except ValueError:
                        pass
                    break
            clen = min(clen, _MAX_BUF - len(body_bytes))
            while len(body_bytes) < clen:
                try:
                    chunk = conn.recv(min(_CHUNK, clen - len(body_bytes)))
                except OSError:
                    break
                if not chunk:
                    break
                body_bytes += chunk

            body = body_bytes.decode('utf-8', 'ignore') if body_bytes else ''
            self._dispatch(conn, method, fullpath, header_str, body)

        except Exception as e:
            print(f'WEB Request error: {e}')
        finally:
            try:
                conn.close()
            except Exception:
                pass
            gc.collect()

    # ── Token auth ────────────────────────────────────────────────────────────

    def _auth_ok(self, headers: str) -> bool:
        if self._token is None:
            return False
        # Check token TTL
        ttl = self._cfg.get('token_ttl_seconds', 86400)
        if ttl > 0 and utime.time() - self._token_ts > ttl:
            print('WEB Token expired')
            self._token = None
            return False
        for line in headers.split('\r\n'):
            if line.lower().startswith('x-auth-token'):
                return line.split(':', 1)[1].strip() == self._token
        return False

    # ── Response helpers ──────────────────────────────────────────────────────

    def _cors_headers(self):
        return 'Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: Content-Type, X-Auth-Token\r\n'

    def _send_json(self, conn, data, status: int = 200):
        body   = ujson.dumps(data).encode()
        reason = _HTTP_REASON.get(status, 'OK')
        header = (
            f'HTTP/1.1 {status} {reason}\r\n'
            f'Content-Type: application/json\r\n'
            f'Content-Length: {len(body)}\r\n'
            f'{self._cors_headers()}\r\n'
        ).encode()
        conn.send(header)
        conn.send(body)

    def _send_text(self, conn, text: str, status: int = 200):
        b      = text.encode('utf-8')
        reason = _HTTP_REASON.get(status, 'OK')
        header = (
            f'HTTP/1.1 {status} {reason}\r\n'
            f'Content-Type: text/plain; charset=utf-8\r\n'
            f'Content-Length: {len(b)}\r\n'
            f'{self._cors_headers()}\r\n'
        ).encode()
        conn.send(header)
        conn.send(b)

    def _send_html(self, conn, html: str):
        b      = html.encode('utf-8')
        header = (
            f'HTTP/1.1 200 OK\r\n'
            f'Content-Type: text/html; charset=utf-8\r\n'
            f'Content-Length: {len(b)}\r\n\r\n'
        ).encode()
        conn.send(header)
        conn.send(b)

    def _send_compressed_html(self, conn):
        """Send pre-compressed dashboard with Content-Encoding: gzip."""
        global _DASHBOARD_GZ
        if _DASHBOARD_GZ is None:
            self._send_text(conn, 'Dashboard not available.', status=404)
            return
        header = (
            f'HTTP/1.1 200 OK\r\n'
            f'Content-Type: text/html; charset=utf-8\r\n'
            f'Content-Encoding: gzip\r\n'
            f'Content-Length: {len(_DASHBOARD_GZ)}\r\n\r\n'
        ).encode()
        conn.send(header)
        conn.send(_DASHBOARD_GZ)

    def _send_401(self, conn):
        self._send_json(conn, {'error': 'Unauthorized'}, status=401)

    def _send_file(self, conn, data: bytes, filename: str):
        header = (
            f'HTTP/1.1 200 OK\r\n'
            f'Content-Type: application/json\r\n'
            f'Content-Disposition: attachment; filename={filename}\r\n'
            f'Content-Length: {len(data)}\r\n'
            f'{self._cors_headers()}\r\n'
        ).encode()
        conn.send(header)
        conn.send(data)

    # ── Uptime formatter ──────────────────────────────────────────────────────

    def _uptime_str(self) -> str:
        secs = utime.ticks_diff(utime.ticks_ms(), self._boot_ticks) // 1000
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        if h > 0:
            return f'{h}h {m}m {s}s'
        elif m > 0:
            return f'{m}m {s}s'
        return f'{s}s'

    # ── Dispatcher ────────────────────────────────────────────────────────────

    def _dispatch(self, conn, method: str, fullpath: str, headers: str, body: str):
        path  = fullpath.split('?')[0]
        query = fullpath.split('?')[1] if '?' in fullpath else ''

        # ── Public endpoints (no auth required) ──────────────────────────────

        if method == 'GET' and path == '/':
            self._send_compressed_html(conn)
            return

        if method == 'GET' and path == '/nonce':
            self._send_json(conn, {'nonce': self._nonce})
            return

        if method == 'POST' and path == '/login':
            now = utime.time()
            # Rate limiting check
            lockout_secs = self._cfg.get('login_lockout_secs', 60)
            max_attempts = self._cfg.get('login_max_attempts', 5)
            if now < self._lockout_until:
                remaining = self._lockout_until - now
                self._send_json(conn, {
                    'ok': False,
                    'error': f'Account locked. Try again in {remaining}s.'
                }, status=429)
                return
            try:
                d    = ujson.loads(body)
                user = self._cfg.get('auth_user', 'admin')
                pw   = self._cfg.get('auth_pass', 'admin123')
                pw_h = _sha256hex(pw)
                exp  = _sha256hex(f'{user}{pw_h}{self._nonce}')
                if d.get('user') == user and d.get('token') == exp:
                    # FIX: store the computed expected token, not client-supplied value
                    self._token    = exp
                    self._token_ts = now
                    self._nonce    = _boot_nonce()   # rotate nonce after each login
                    self._login_attempts = 0
                    self._send_json(conn, {'ok': True})
                else:
                    self._login_attempts += 1
                    if self._login_attempts >= max_attempts:
                        self._lockout_until = now + lockout_secs
                        self._login_attempts = 0
                        print(f'WEB Login locked out for {lockout_secs}s')
                    self._send_json(conn, {'ok': False}, status=401)
            except Exception as e:
                self._send_json(conn, {'ok': False, 'error': str(e)}, status=400)
            return

        # ── All protected endpoints require a valid token ─────────────────────

        if not self._auth_ok(headers):
            self._send_401(conn)
            return

        gc.collect()

        if method == 'GET' and path == '/status':
            t   = utime.localtime()
            day = _DAYS[t[6]] if t[6] < 7 else 'Sunday'
            resp = {
                'version':       _VERSION,
                'uptime':        self._uptime_str(),
                'time':          f'{t[3]:02d}:{t[4]:02d}:{t[5]:02d}',
                'date':          f'{t[0]}-{t[1]:02d}-{t[2]:02d}',
                'day':           day,
                'ntp_synced':    self._ntp.is_synced(),
                'ntp_last_sync': self._ntp.last_sync_str(),
                'ip':            self._wifi.get_ip(),
                'bell_ringing':  self._bell.is_ringing(),
                'next_bell':     self._sched.get_next_event(),
            }
            if self._rtc:
                resp['rtc_available'] = self._rtc.is_available()
                resp['rtc_power_fail'] = self._rtc.has_power_fail()
                resp['rtc_status'] = self._rtc.status_str()
            self._send_json(conn, resp)

        elif method == 'GET' and path == '/schedule':
            day = next(
                (p.split('=', 1)[1] for p in query.split('&') if p.startswith('day=')),
                None
            )
            if day:
                day = day.replace('+', ' ')
                self._send_json(conn, {
                    'day':    day,
                    'events': self._sched.get_day_schedule(day),
                })
            else:
                self._send_json(conn, {'schedule': self._sched.get_schedule()})

        elif method == 'GET' and path == '/config':
            cfg = self._cfg.get_public()
            self._send_json(conn, cfg)

        elif method == 'GET' and path == '/config/backup':
            backup = {
                'config':   self._cfg.get_public(),
                'schedule': self._sched.get_schedule(),
                'holidays': self._sched.get_holidays(),
            }
            # Strip WiFi password from backup
            backup['config'].pop('wifi_password', None)
            data = ujson.dumps(backup).encode()
            self._send_file(conn, data, 'sbrr_backup.json')

        elif method == 'GET' and path == '/logs':
            lines = self._sched.get_logs(100)
            self._send_text(conn, ''.join(lines) if lines else 'No log entries yet.')

        elif method == 'POST' and path == '/schedule/add':
            try:
                d     = ujson.loads(body)
                ok, m = self._sched.add_event(
                    d['day'], d['time'], d['event_name'],
                    d.get('bell_pattern', 'single_ring'),
                    d.get('duration_seconds', 3),
                )
                self._send_json(conn, {'success': ok, 'message': m, 'day': d.get('day')})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'PUT' and path == '/schedule/event':
            try:
                d     = ujson.loads(body)
                day   = d['day']
                old_t = d.get('old_time', d.get('time'))
                new_d = {
                    'time':             d.get('time'),
                    'event_name':       d.get('event_name'),
                    'bell_pattern':     d.get('bell_pattern'),
                    'duration_seconds': d.get('duration_seconds'),
                }
                ok, m = self._sched.edit_event(day, old_t, new_d)
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/delete':
            try:
                d     = ujson.loads(body)
                ok, m = self._sched.delete_event(d['day'], d['time'])
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/upload':
            try:
                ok, m = self._sched.upload_schedule(body)
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/copy':
            try:
                d     = ujson.loads(body)
                ok, m = self._sched.copy_day_schedule(d['source_day'], d['target_day'])
                count = 0
                if ok:
                    count = len(self._sched.get_day_schedule(d['target_day']))
                self._send_json(conn, {'success': ok, 'message': m, 'count': count})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'GET' and path == '/schedule/export':
            data = ujson.dumps({
                'schedule': self._sched.get_schedule(),
                'holidays': self._sched.get_holidays(),
            }).encode()
            self._send_file(conn, data, 'schedule_export.json')

        elif method == 'POST' and path == '/schedule/holiday':
            try:
                d  = ujson.loads(body)
                ok, m = self._sched.add_holiday(d['date'])
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/holiday/remove':
            try:
                d  = ujson.loads(body)
                ok, m = self._sched.remove_holiday(d['date'])
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'GET' and path == '/schedule/holidays':
            self._send_json(conn, {'holidays': self._sched.get_holidays()})

        elif method == 'POST' and path == '/bell/ring':
            try:
                d = ujson.loads(body)
                max_dur = self._cfg.get('max_bell_duration', 30)
                duration = min(int(d.get('duration', 3)), max_dur)
                self._bell.ring(
                    d.get('pattern',  'single_ring'),
                    duration,
                )
                self._send_json(conn, {
                    'success': True,
                    'message': f"Ringing {d.get('pattern','single_ring')} "
                               f"for {duration}s",
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/bell/stop':
            self._bell.stop()
            self._send_json(conn, {'success': True, 'message': 'Bell stopped.'})

        elif method == 'POST' and path == '/config/update':
            try:
                d = ujson.loads(body)
                updates = {}
                for k in ('auth_user', 'ntp_host', 'ntp_interval_hours',
                          'allowed_cidr', 'max_bell_duration',
                          'login_max_attempts', 'login_lockout_secs',
                          'token_ttl_seconds', 'web_port',
                          'watchdog_timeout_ms'):
                    if k in d and d[k] not in ('', None):
                        updates[k] = d[k]
                # NTP mode toggle
                if 'ntp_use_http' in d:
                    updates['ntp_use_http'] = bool(d['ntp_use_http'])
                # Only update password if a new value was provided
                if d.get('auth_pass'):
                    updates['auth_pass'] = d['auth_pass']
                self._cfg.update_many(updates)
                if d.get('wifi_ssid'):
                    self._cfg.update_wifi(
                        d['wifi_ssid'],
                        d.get('wifi_password', self._cfg.get_wifi('password', '')),
                    )
                # Invalidate session on any config change
                self._token = None
                self._nonce = _boot_nonce()
                self._send_json(conn, {
                    'success': True,
                    'message': 'Config saved. Session invalidated — please log in again.',
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        else:
            conn.send(b'HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found')

# web_server.py  v1.0.0
# Production: Non-blocking single-connection HTTP server on RP2350/Pico W.
# Auth: nonce-based SHA-256 challenge (no plaintext password over network).
# ACL:  CIDR-based IP allowlist; '0.0.0.0/0' allows all (default).
# UI:   Single-page dashboard — timetable, event CRUD, bell control, config, logs.

import usocket
import ujson
import utime
import gc
import uos
import uhashlib
import ubinascii
from micropython import const

_DAYS    = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_MAX_BUF = const(2048)
_CHUNK   = const(256)
_TIMEOUT = 3

_HTTP_REASON = {
    200: 'OK',
    400: 'Bad Request',
    401: 'Unauthorized',
    404: 'Not Found',
    405: 'Method Not Allowed',
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


# ── Embedded HTML & JavaScript ────────────────────────────────────────────────

_LOGIN_OVERLAY = (
    '<div id="lo" style="position:fixed;top:0;left:0;width:100%;height:100%;'
    'background:#1a237e;display:flex;align-items:center;justify-content:center;z-index:9999">'
    '<div style="background:#fff;border-radius:8px;padding:32px;width:300px;text-align:center">'
    '<h2 style="color:#1a237e;margin-bottom:20px">&#x1F512; SBRR Bell System</h2>'
    '<input type="text" id="lu" placeholder="Username" autocomplete="username" '
    'style="display:block;width:100%;margin-bottom:8px;padding:10px;border:1px solid #ccc;'
    'border-radius:4px;font-size:14px">'
    '<input type="password" id="lp" placeholder="Password" autocomplete="current-password" '
    'style="display:block;width:100%;margin-bottom:8px;padding:10px;border:1px solid #ccc;'
    'border-radius:4px;font-size:14px">'
    '<div id="lm" style="color:#c62828;font-size:13px;min-height:18px;margin-bottom:8px"></div>'
    '<button onclick="doLogin()" style="background:#1a237e;color:#fff;border:none;'
    'padding:10px 28px;border-radius:4px;cursor:pointer;font-size:15px;width:100%">Login</button>'
    '</div></div>'
)

_HTML = (
    '<!DOCTYPE html><html lang="en"><head>'
    '<meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>SBRR Bell System</title>'
    '<style>'
    '*{box-sizing:border-box;margin:0;padding:0}'
    'body{font-family:Arial,sans-serif;background:#f0f2f5}'
    '.hdr{background:#1a237e;color:#fff;padding:14px 20px;text-align:center}'
    '.hdr h1{font-size:18px}.hdr p{font-size:12px;opacity:.8;margin-top:4px}'
    '.wrap{max-width:960px;margin:16px auto;padding:0 12px}'
    '.card{background:#fff;border-radius:8px;padding:16px;margin-bottom:14px;'
    'box-shadow:0 2px 6px rgba(0,0,0,.09)}'
    '.card h2{font-size:15px;color:#1a237e;border-bottom:2px solid #e8eaf6;'
    'padding-bottom:7px;margin-bottom:10px}'
    '.stat-row{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}'
    '.stat{background:#e8eaf6;padding:10px;border-radius:6px}'
    '.stat label{font-size:11px;color:#555;display:block;margin-bottom:2px}'
    '.stat span{font-size:16px;font-weight:700;color:#1a237e}'
    'table{width:100%;border-collapse:collapse;font-size:13px}'
    'th{background:#1a237e;color:#fff;padding:8px 6px;text-align:left}'
    'td{padding:6px;border-bottom:1px solid #eee}'
    'tr:hover td{background:#f5f5f5}'
    '.btn{padding:7px 14px;border:none;border-radius:4px;cursor:pointer;'
    'font-size:13px;font-weight:600}'
    '.pr{background:#1a237e;color:#fff}.dr{background:#c62828;color:#fff}'
    '.gr{background:#2e7d32;color:#fff}.or{background:#e65100;color:#fff}'
    'input,select,textarea{padding:7px;border:1px solid #ccc;border-radius:4px;'
    'font-size:13px;width:100%;margin-bottom:7px}'
    '.fr2{display:grid;grid-template-columns:1fr 1fr;gap:8px}'
    '.alert{padding:9px;border-radius:4px;margin-bottom:10px;font-size:13px}'
    '.ok{background:#e8f5e9;color:#2e7d32;border:1px solid #a5d6a7}'
    '.er{background:#ffebee;color:#c62828;border:1px solid #ef9a9a}'
    '.tabs{display:flex;gap:4px;margin-bottom:14px;flex-wrap:wrap}'
    '.tab{padding:7px 14px;background:#e8eaf6;border:none;border-radius:4px;'
    'cursor:pointer;font-size:13px}'
    '.tab.active,.tab:hover{background:#1a237e;color:#fff}'
    '.tc{display:none}.tc.active{display:block}'
    '</style></head><body>'
    'LOGINOVERLAY'
    '<div class="hdr">'
    '<h1>&#x1F514; SBRR Mahajana Bell Automation System</h1>'
    '<p>Department of BCA in Artificial Intelligence &mdash; II Sem 2025-26</p>'
    '</div>'
    '<div class="wrap">'

    # Status bar
    '<div class="card"><div class="stat-row">'
    '<div class="stat"><label>Current Time</label><span id="ct">--:--:--</span></div>'
    '<div class="stat"><label>Day</label><span id="cd">---</span></div>'
    '<div class="stat"><label>NTP Last Sync</label><span id="ns">---</span></div>'
    '<div class="stat"><label>Next Bell</label><span id="nb">---</span></div>'
    '</div></div>'

    # Tab buttons
    '<div class="tabs">'
    '<button class="tab active" onclick="tab(\'sched\')">Timetable</button>'
    '<button class="tab" onclick="tab(\'add\')">Add Event</button>'
    '<button class="tab" onclick="tab(\'ctl\')">Bell Control</button>'
    '<button class="tab" onclick="tab(\'cfg\')">Config</button>'
    '<button class="tab" onclick="tab(\'log\')">Logs</button>'
    '</div>'

    # Timetable tab
    '<div id="t-sched" class="tc active card">'
    '<h2>Timetable Viewer</h2>'
    '<div id="ms"></div>'
    '<select id="dsel" onchange="loadDay(this.value)" '
    'style="width:auto;margin-bottom:10px">'
    '<option>Monday</option><option>Tuesday</option><option>Wednesday</option>'
    '<option>Thursday</option><option>Friday</option><option>Saturday</option>'
    '</select>'
    '<table><thead><tr>'
    '<th>Time</th><th>Event</th><th>Pattern</th><th>Duration (s)</th><th></th>'
    '</tr></thead><tbody id="sb"></tbody></table>'
    '</div>'

    # Add Event tab
    '<div id="t-add" class="tc card">'
    '<h2>Add / Upload Schedule</h2>'
    '<div id="ma"></div>'
    '<div class="fr2">'
    '<div><label>Day</label><select id="ad">'
    '<option>Monday</option><option>Tuesday</option><option>Wednesday</option>'
    '<option>Thursday</option><option>Friday</option><option>Saturday</option>'
    '</select></div>'
    '<div><label>Time (HH:MM)</label><input type="time" id="at"></div>'
    '</div>'
    '<label>Event Name</label>'
    '<input type="text" id="an" placeholder="e.g. First Period Start">'
    '<div class="fr2">'
    '<div><label>Bell Pattern</label><select id="ap">'
    '<option value="single_ring">Single Ring</option>'
    '<option value="double_ring">Double Ring</option>'
    '<option value="long_ring">Long Ring</option>'
    '<option value="triple_ring">Triple Ring</option>'
    '<option value="custom_pattern">Custom Pattern</option>'
    '</select></div>'
    '<div><label>Duration (sec)</label>'
    '<input type="number" id="adur" value="5" min="1" max="30"></div>'
    '</div>'
    '<button class="btn pr" onclick="addEvent()">Add Event</button>'
    '<hr style="margin:14px 0">'
    '<h2>Upload Full Schedule JSON</h2>'
    '<textarea id="uj" rows="5" '
    'placeholder=\'{"schedule":{"Monday":[...]},"holidays":["YYYY-MM-DD"]}\'>'
    '</textarea>'
    '<button class="btn or" onclick="uploadSched()">Upload / Replace Schedule</button>'
    '</div>'

    # Bell Control tab
    '<div id="t-ctl" class="tc card">'
    '<h2>Manual Bell Control</h2>'
    '<div id="mb"></div>'
    '<div class="fr2">'
    '<div><label>Pattern</label><select id="bp">'
    '<option value="single_ring">Single Ring</option>'
    '<option value="double_ring">Double Ring</option>'
    '<option value="long_ring">Long Ring</option>'
    '<option value="triple_ring">Triple Ring</option>'
    '<option value="custom_pattern">Custom Pattern</option>'
    '</select></div>'
    '<div><label>Duration (sec)</label>'
    '<input type="number" id="bd" value="3" min="1" max="30"></div>'
    '</div>'
    '<button class="btn gr" onclick="ringBell()">&#x1F514; Ring Bell Now</button>&nbsp;'
    '<button class="btn pr" onclick="testBell()">Test 1s</button>&nbsp;'
    '<button class="btn dr" onclick="stopBell()">Stop</button>'
    '</div>'

    # Config tab
    '<div id="t-cfg" class="tc card">'
    '<h2>System Configuration</h2>'
    '<div id="mc"></div>'
    '<div class="fr2">'
    '<div><label>Admin Username</label><input type="text" id="cu"></div>'
    '<div><label>Admin Password</label><input type="password" id="cp" '
    'placeholder="(unchanged if blank)"></div>'
    '</div>'
    '<div class="fr2">'
    '<div><label>NTP Host</label><input type="text" id="nh"></div>'
    '<div><label>NTP Interval (hrs)</label>'
    '<input type="number" id="ni" min="1" max="24"></div>'
    '</div>'
    '<div class="fr2">'
    '<div><label>WiFi SSID</label><input type="text" id="ws"></div>'
    '<div><label>WiFi Password</label><input type="password" id="wp" '
    'placeholder="(unchanged if blank)"></div>'
    '</div>'
    '<label>Allowed CIDR</label>'
    '<input type="text" id="ac" placeholder="0.0.0.0/0">'
    '<button class="btn pr" onclick="saveCfg()">Save Config</button>&nbsp;'
    '<button class="btn gr" onclick="dlBackup()">Download Backup</button>'
    '<hr style="margin:14px 0">'
    '<h2>Holiday Override</h2>'
    '<div class="fr2">'
    '<div><label>Date (YYYY-MM-DD)</label><input type="date" id="hd"></div>'
    '<div></div>'
    '</div>'
    '<button class="btn or" onclick="addHoliday()">Mark as Holiday (no bells)</button>'
    '</div>'

    # Logs tab
    '<div id="t-log" class="tc card">'
    '<h2>Bell Event Logs</h2>'
    '<button class="btn pr" onclick="loadLogs()">Refresh Logs</button>'
    '<pre id="lc" style="font-size:11px;background:#f5f5f5;padding:10px;'
    'border-radius:4px;max-height:340px;overflow-y:auto;margin-top:10px;'
    'white-space:pre-wrap">Press Refresh to load…</pre>'
    '</div>'

    '</div>'  # end .wrap

    # JavaScript
    '<script>'
    'var tok=sessionStorage.getItem("tok");'

    'function H(){return{"Content-Type":"application/json","X-Auth-Token":tok}}'

    'function msg(id,t,ok){'
    'var e=document.getElementById(id);'
    'e.className="alert "+(ok?"ok":"er");'
    'e.textContent=t;'
    'setTimeout(function(){e.textContent="";e.className=""},5000)'
    '}'

    'function tab(n){'
    'document.querySelectorAll(".tab").forEach(function(e){e.classList.remove("active")});'
    'document.querySelectorAll(".tc").forEach(function(e){e.classList.remove("active")});'
    'var sel=document.querySelector(".tab[onclick=\\"tab(\'"+n+"\')\\""]");'
    'if(sel)sel.classList.add("active");'
    'var tc=document.getElementById("t-"+n);'
    'if(tc)tc.classList.add("active")'
    '}'

    'async function sha256hex(s){'
    'var buf=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s));'
    'return Array.from(new Uint8Array(buf)).map(function(b){return b.toString(16).padStart(2,"0")}).join("")'
    '}'

    'async function doLogin(){'
    'var u=document.getElementById("lu").value.trim();'
    'var p=document.getElementById("lp").value;'
    'document.getElementById("lm").textContent="";'
    'if(!u||!p){document.getElementById("lm").textContent="Enter credentials";return}'
    'try{'
    'var nr=await fetch("/nonce"),nd=await nr.json();'
    'var ph=await sha256hex(p);'
    'var tv=await sha256hex(u+ph+nd.nonce);'
    'var r=await fetch("/login",{method:"POST",'
    'headers:{"Content-Type":"application/json"},'
    'body:JSON.stringify({user:u,token:tv})});'
    'var d=await r.json();'
    'if(d.ok){'
    'tok=tv;sessionStorage.setItem("tok",tok);'
    'document.getElementById("lo").style.display="none";'
    'loadAll()'
    '}else{'
    'document.getElementById("lm").textContent="Invalid credentials"'
    '}'
    '}catch(e){'
    'document.getElementById("lm").textContent="Connection error: "+e'
    '}'
    '}'

    'function loadAll(){pollStatus();loadDay(document.getElementById("dsel").value||"Monday");loadCfg()}'

    'function pollStatus(){'
    'fetch("/status",{headers:H()})'
    '.then(function(r){if(r.status===401){logout();return null}return r.json()})'
    '.then(function(d){'
    'if(!d||!d.time)return;'
    'document.getElementById("ct").textContent=d.time;'
    'document.getElementById("cd").textContent=d.day;'
    'document.getElementById("ns").textContent=d.ntp_last_sync;'
    'var nb=d.next_bell;'
    'document.getElementById("nb").textContent='
    'nb?(nb.day+" "+nb.event.time+" — "+nb.event.event_name):"None"'
    '}).catch(function(){})'
    '}'

    'function logout(){'
    'tok=null;sessionStorage.removeItem("tok");'
    'document.getElementById("lo").style.display="flex"'
    '}'

    'function loadDay(day){'
    'fetch("/schedule?day="+encodeURIComponent(day),{headers:H()})'
    '.then(function(r){return r.json()})'
    '.then(function(d){'
    'var h="";'
    '(d.events||[]).forEach(function(e){'
    'h+="<tr>"'
    '+"<td>"+e.time+"</td>"'
    '+"<td>"+e.event_name+"</td>"'
    '+"<td>"+e.bell_pattern+"</td>"'
    '+"<td>"+e.duration_seconds+"</td>"'
    '+"<td><button class=\'btn dr\' onclick=\'del(\\""+day+'
    '"\\"\\",\\""+e.time+"\\")\'>&times; Del</button></td>"'
    '+"</tr>"'
    '});'
    'document.getElementById("sb").innerHTML='
    'h||"<tr><td colspan=5 style=text-align:center>No events scheduled</td></tr>"'
    '})'
    '}'

    'function del(day,time){'
    'if(!confirm("Delete event on "+day+" at "+time+"?"))return;'
    'fetch("/schedule/delete",{method:"POST",headers:H(),'
    'body:JSON.stringify({day:day,time:time})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ms",d.message,d.success);if(d.success)loadDay(day)})'
    '}'

    'function addEvent(){'
    'var tv=document.getElementById("at").value;'
    'if(tv&&tv.indexOf(":")>=0){'
    'var parts=tv.split(":");tv=parts[0].padStart(2,"0")+":"+parts[1].padStart(2,"0")'
    '}'
    'var d={'
    'day:document.getElementById("ad").value,'
    'time:tv,'
    'event_name:document.getElementById("an").value.trim(),'
    'bell_pattern:document.getElementById("ap").value,'
    'duration_seconds:parseInt(document.getElementById("adur").value)||3'
    '};'
    'if(!d.time||!d.event_name){msg("ma","Please fill all fields",false);return}'
    'fetch("/schedule/add",{method:"POST",headers:H(),body:JSON.stringify(d)})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ma",d.message,d.success);if(d.success)loadDay(d.day||document.getElementById("ad").value)})'
    '}'

    'function uploadSched(){'
    'var j=document.getElementById("uj").value.trim();'
    'if(!j){msg("ma","Paste schedule JSON first",false);return}'
    'try{JSON.parse(j)}catch(e){msg("ma","Invalid JSON: "+e,false);return}'
    'fetch("/schedule/upload",{method:"POST",headers:H(),body:j})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ma",d.message,d.success)})'
    '}'

    'function ringBell(){'
    'fetch("/bell/ring",{method:"POST",headers:H(),'
    'body:JSON.stringify({'
    'pattern:document.getElementById("bp").value,'
    'duration:parseInt(document.getElementById("bd").value)||3'
    '})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mb",d.message,d.success)})'
    '}'

    'function testBell(){'
    'fetch("/bell/ring",{method:"POST",headers:H(),'
    'body:JSON.stringify({pattern:"single_ring",duration:1})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mb","Test: "+d.message,d.success)})'
    '}'

    'function stopBell(){'
    'fetch("/bell/stop",{method:"POST",headers:H(),body:"{}"})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mb",d.message,d.success)})'
    '}'

    'function saveCfg(){'
    'var d={'
    'auth_user:document.getElementById("cu").value.trim(),'
    'auth_pass:document.getElementById("cp").value,'
    'ntp_host:document.getElementById("nh").value.trim(),'
    'ntp_interval_hours:parseInt(document.getElementById("ni").value)||1,'
    'wifi_ssid:document.getElementById("ws").value.trim(),'
    'wifi_password:document.getElementById("wp").value,'
    'allowed_cidr:document.getElementById("ac").value.trim()'
    '};'
    'fetch("/config/update",{method:"POST",headers:H(),body:JSON.stringify(d)})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mc",d.message,d.success)})'
    '}'

    'function dlBackup(){window.open("/config/backup","_blank")}'

    'function addHoliday(){'
    'var d=document.getElementById("hd").value;'
    'if(!d){msg("mc","Select a date first",false);return}'
    'fetch("/schedule/holiday",{method:"POST",headers:H(),'
    'body:JSON.stringify({date:d})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mc",d.message,d.success)})'
    '}'

    'function loadLogs(){'
    'fetch("/logs",{headers:H()})'
    '.then(function(r){return r.text()})'
    '.then(function(t){'
    'document.getElementById("lc").textContent=t.trim()||"No log entries yet."'
    '})'
    '}'

    'function loadCfg(){'
    'fetch("/config",{headers:H()})'
    '.then(function(r){return r.json()})'
    '.then(function(d){'
    'document.getElementById("cu").value=d.auth_user||"";'
    'document.getElementById("nh").value=d.ntp_host||"pool.ntp.org";'
    'document.getElementById("ni").value=d.ntp_interval_hours||1;'
    'document.getElementById("ac").value=d.allowed_cidr||"0.0.0.0/0"'
    '})'
    '}'

    'window.addEventListener("load",function(){'
    'if(!tok)return;'
    'fetch("/status",{headers:H()})'
    '.then(function(r){if(r.status===401)logout();'
    'else{document.getElementById("lo").style.display="none";loadAll()}})'
    '.catch(function(){logout()})'
    '});'

    'setInterval(pollStatus,5000);'

    '</script></body></html>'
)


class WebServer:
    def __init__(self, cfg, scheduler, bell, ntp, wifi):
        self._cfg     = cfg
        self._sched   = scheduler
        self._bell    = bell
        self._ntp     = ntp
        self._wifi    = wifi
        self._sock    = None
        self._running = False
        self._nonce   = _boot_nonce()
        self._token   = None   # None until first successful login

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
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

            if method not in ('GET', 'POST', 'HEAD'):
                conn.send(b'HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n')
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
        for line in headers.split('\r\n'):
            if line.lower().startswith('x-auth-token'):
                return line.split(':', 1)[1].strip() == self._token
        return False

    # ── Response helpers ──────────────────────────────────────────────────────

    def _send_json(self, conn, data, status: int = 200):
        body   = ujson.dumps(data).encode()
        reason = _HTTP_REASON.get(status, 'OK')
        header = (
            f'HTTP/1.1 {status} {reason}\r\n'
            f'Content-Type: application/json\r\n'
            f'Content-Length: {len(body)}\r\n'
            f'Access-Control-Allow-Origin: *\r\n\r\n'
        ).encode()
        conn.send(header)
        conn.send(body)

    def _send_text(self, conn, text: str, status: int = 200):
        b      = text.encode('utf-8')
        reason = _HTTP_REASON.get(status, 'OK')
        header = (
            f'HTTP/1.1 {status} {reason}\r\n'
            f'Content-Type: text/plain; charset=utf-8\r\n'
            f'Content-Length: {len(b)}\r\n\r\n'
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

    def _send_401(self, conn):
        self._send_json(conn, {'error': 'Unauthorized'}, status=401)

    def _send_file(self, conn, data: bytes, filename: str):
        header = (
            f'HTTP/1.1 200 OK\r\n'
            f'Content-Type: application/json\r\n'
            f'Content-Disposition: attachment; filename={filename}\r\n'
            f'Content-Length: {len(data)}\r\n\r\n'
        ).encode()
        conn.send(header)
        conn.send(data)

    # ── Dispatcher ────────────────────────────────────────────────────────────

    def _dispatch(self, conn, method: str, fullpath: str, headers: str, body: str):
        path  = fullpath.split('?')[0]
        query = fullpath.split('?')[1] if '?' in fullpath else ''

        # ── Public endpoints (no auth required) ──────────────────────────────

        if method == 'GET' and path == '/':
            self._send_html(conn, _HTML.replace('LOGINOVERLAY', _LOGIN_OVERLAY))
            return

        if method == 'GET' and path == '/nonce':
            self._send_json(conn, {'nonce': self._nonce})
            return

        if method == 'POST' and path == '/login':
            try:
                d    = ujson.loads(body)
                user = self._cfg.get('auth_user', 'admin')
                pw   = self._cfg.get('auth_pass', 'admin123')
                pw_h = _sha256hex(pw)
                exp  = _sha256hex(f'{user}{pw_h}{self._nonce}')
                if d.get('user') == user and d.get('token') == exp:
                    self._token = d['token']
                    self._send_json(conn, {'ok': True})
                else:
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
            self._send_json(conn, {
                'time':          f'{t[3]:02d}:{t[4]:02d}:{t[5]:02d}',
                'date':          f'{t[0]}-{t[1]:02d}-{t[2]:02d}',
                'day':           day,
                'ntp_synced':    self._ntp.is_synced(),
                'ntp_last_sync': self._ntp.last_sync_str(),
                'ip':            self._wifi.get_ip(),
                'bell_ringing':  self._bell.is_ringing(),
                'next_bell':     self._sched.get_next_event(),
            })

        elif method == 'GET' and path == '/schedule':
            day = next(
                (p.split('=', 1)[1] for p in query.split('&') if p.startswith('day=')),
                None
            )
            if day:
                # URL-decode '+' to space
                day = day.replace('+', ' ')
                self._send_json(conn, {
                    'day':    day,
                    'events': self._sched.get_day_schedule(day),
                })
            else:
                self._send_json(conn, {'schedule': self._sched.get_schedule()})

        elif method == 'GET' and path == '/config':
            cfg = self._cfg.get_all()
            cfg.pop('auth_pass', None)
            self._send_json(conn, cfg)

        elif method == 'GET' and path == '/config/backup':
            backup = {
                'config':   self._cfg.get_all(),
                'schedule': self._sched.get_schedule(),
            }
            backup['config'].pop('auth_pass', None)
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

        elif method == 'POST' and path == '/schedule/edit':
            try:
                d     = ujson.loads(body)
                ok, m = self._sched.edit_event(d['day'], d['time'], d)
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

        elif method == 'POST' and path == '/schedule/holiday':
            try:
                d  = ujson.loads(body)
                ok = self._sched.add_holiday(d['date'])
                self._send_json(conn, {
                    'success': ok,
                    'message': 'Holiday marked — bells will be suppressed.' if ok
                               else 'Already marked as holiday.',
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/bell/ring':
            try:
                d = ujson.loads(body)
                self._bell.ring(
                    d.get('pattern',  'single_ring'),
                    d.get('duration', 3),
                )
                self._send_json(conn, {
                    'success': True,
                    'message': f"Ringing {d.get('pattern','single_ring')} "
                               f"for {d.get('duration',3)}s",
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/bell/stop':
            self._bell.stop()
            self._send_json(conn, {'success': True, 'message': 'Bell stopped.'})

        elif method == 'POST' and path == '/config/update':
            try:
                d = ujson.loads(body)
                for k in ('auth_user', 'ntp_host', 'ntp_interval_hours', 'allowed_cidr'):
                    if k in d and d[k] not in ('', None):
                        self._cfg.update(k, d[k])
                # Only update password if a new value was provided
                if d.get('auth_pass'):
                    self._cfg.update('auth_pass', d['auth_pass'])
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

# web_server.py  v2.2.0
# FIX 7: _REASON map — send_json uses correct HTTP reason phrase per status code
# FIX 8: All send_*() helpers use \r\n (CRLF) headers + \r\n\r\n header terminator
# FIX 9: ip_allowed() fallback cidr = '0.0.0.0/0'  (was '192.168.1.0/24')
# FIX 10: addEvent() JS strips ':' from <input type="time"> → HHMM format

import usocket, ujson, utime, gc, uos, uhashlib, ubinascii
from micropython import const

_DAYS    = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
_MAX_BUF = const(2048)
_CHUNK   = const(256)
_TIMEOUT = 3

_REASON = {
    200: 'OK',
    400: 'Bad Request',
    401: 'Unauthorized',
    404: 'Not Found',
    405: 'Method Not Allowed'
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
        return ubinascii.hexlify(machine.unique_id() * 2).decode()


# ── CIDR subnet validation ────────────────────────────────────────────────────
def _ip_to_int(ip: str) -> int:
    parts = ip.split('.')
    if len(parts) != 4:
        raise ValueError('Bad IP')
    n = 0
    for p in parts:
        v = int(p)
        if v < 0 or v > 255:
            raise ValueError('Octet range')
        n = (n << 8) | v
    return n

def _cidr_allows(ip: str, cidr: str) -> bool:
    try:
        net, bits = cidr.split('/')
        bits = int(bits)
        if bits < 0 or bits > 32:
            return False
        mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
        return (_ip_to_int(ip) & mask) == (_ip_to_int(net) & mask)
    except Exception:
        return False


# ── Embedded HTML ─────────────────────────────────────────────────────────────
_LOGIN_OVERLAY = (
    '<div id="lo" style="position:fixed;top:0;left:0;width:100%;height:100%;'
    'background:#1a237e;display:flex;align-items:center;justify-content:center;z-index:9999">'
    '<div style="background:#fff;border-radius:8px;padding:32px;width:300px;text-align:center">'
    '<h2 style="color:#1a237e;margin-bottom:20px">&#x1F512; SBRR Bell System</h2>'
    '<input type="text" id="lu" placeholder="Username" style="display:block;width:100%;'
    'margin-bottom:8px;padding:10px;border:1px solid #ccc;border-radius:4px;font-size:14px">'
    '<input type="password" id="lp" placeholder="Password" style="display:block;width:100%;'
    'margin-bottom:8px;padding:10px;border:1px solid #ccc;border-radius:4px;font-size:14px">'
    '<div id="lm" style="color:#c62828;font-size:13px;min-height:18px;margin-bottom:8px"></div>'
    '<button onclick="doLogin()" style="background:#1a237e;color:#fff;border:none;'
    'padding:10px 28px;border-radius:4px;cursor:pointer;font-size:15px;width:100%">Login</button>'
    '</div></div>'
)

_DASHBOARD_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>SBRR Bell System</title><style>'
    '*{box-sizing:border-box;margin:0;padding:0}'
    'body{font-family:Arial,sans-serif;background:#f0f2f5}'
    '.hdr{background:#1a237e;color:#fff;padding:14px 20px;text-align:center}'
    '.hdr h1{font-size:18px}.hdr p{font-size:12px;opacity:.8;margin-top:4px}'
    '.wrap{max-width:920px;margin:16px auto;padding:0 12px}'
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
    '.btn{padding:7px 14px;border:none;border-radius:4px;cursor:pointer;font-size:13px;font-weight:600}'
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

    # ── Status bar ────────────────────────────────────────────────────────────
    '<div class="card"><div class="stat-row">'
    '<div class="stat"><label>Current Time</label><span id="ct">------</span></div>'
    '<div class="stat"><label>Day</label><span id="cd">---</span></div>'
    '<div class="stat"><label>NTP Synced</label><span id="ns">---</span></div>'
    '<div class="stat"><label>Next Bell</label><span id="nb">---</span></div>'
    '</div></div>'

    # ── Tab buttons ───────────────────────────────────────────────────────────
    '<div class="tabs">'
    '<button class="tab active" onclick="tab(\'sched\')">Timetable</button>'
    '<button class="tab" onclick="tab(\'add\')">Add Event</button>'
    '<button class="tab" onclick="tab(\'ctl\')">Bell Control</button>'
    '<button class="tab" onclick="tab(\'cfg\')">Config</button>'
    '<button class="tab" onclick="tab(\'log\')">Logs</button>'
    '</div>'

    # ── Timetable tab ─────────────────────────────────────────────────────────
    '<div id="t-sched" class="tc active card">'
    '<h2>Timetable Viewer</h2>'
    '<div id="ms"></div>'
    '<select id="dsel" onchange="loadDay(this.value)" style="width:auto;margin-bottom:10px">'
    '<option>Monday</option><option>Tuesday</option><option>Wednesday</option>'
    '<option>Thursday</option><option>Friday</option><option>Saturday</option>'
    '</select>'
    '<table><thead><tr>'
    '<th>Time</th><th>Event</th><th>Pattern</th><th>Duration(s)</th><th></th>'
    '</tr></thead><tbody id="sb"></tbody></table>'
    '</div>'

    # ── Add Event tab ─────────────────────────────────────────────────────────
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

    # ── Bell Control tab ──────────────────────────────────────────────────────
    '<div id="t-ctl" class="tc card">'
    '<h2>Manual Bell Control</h2>'
    '<div id="mb"></div>'
    '<div class="fr2">'
    '<div><label>Pattern</label><select id="bp">'
    '<option value="single_ring">Single Ring</option>'
    '<option value="double_ring">Double Ring</option>'
    '<option value="long_ring">Long Ring</option>'
    '<option value="triple_ring">Triple Ring</option>'
    '</select></div>'
    '<div><label>Duration (sec)</label>'
    '<input type="number" id="bd" value="3" min="1" max="15"></div>'
    '</div>'
    '<button class="btn gr" onclick="ringBell()">&#x1F514; Ring Bell Now</button>&nbsp;'
    '<button class="btn pr" onclick="testBell()">Test 1s</button>'
    '</div>'

    # ── Config tab ────────────────────────────────────────────────────────────
    '<div id="t-cfg" class="tc card">'
    '<h2>System Configuration</h2>'
    '<div id="mc"></div>'
    '<div class="fr2">'
    '<div><label>Admin User</label><input type="text" id="cu"></div>'
    '<div><label>Admin Password</label><input type="password" id="cp"></div>'
    '</div>'
    '<div class="fr2">'
    '<div><label>NTP Host</label><input type="text" id="nh"></div>'
    '<div><label>NTP Interval (hrs)</label>'
    '<input type="number" id="ni" min="1" max="24"></div>'
    '</div>'
    '<div class="fr2">'
    '<div><label>WiFi SSID</label><input type="text" id="ws"></div>'
    '<div><label>WiFi Password</label><input type="password" id="wp"></div>'
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
    '<button class="btn or" onclick="addHoliday()">Mark as Holiday (no bell)</button>'
    '</div>'

    # ── Logs tab ──────────────────────────────────────────────────────────────
    '<div id="t-log" class="tc card">'
    '<h2>Bell Event Logs</h2>'
    '<button class="btn pr" onclick="loadLogs()">Refresh</button>'
    '<pre id="lc" style="font-size:11px;background:#f5f5f5;padding:10px;'
    'border-radius:4px;max-height:320px;overflow-y:auto;margin-top:10px;'
    'white-space:pre-wrap"></pre>'
    '</div>'

    '</div>'  # end .wrap

    # ── JavaScript ────────────────────────────────────────────────────────────
    '<script>'

    'var tok=sessionStorage.getItem("tok");'

    'function H(){'
    'return{"Content-Type":"application/json","X-Auth-Token":tok}'
    '}'

    'function msg(id,t,ok){'
    'var e=document.getElementById(id);'
    'e.className="alert "+(ok?"ok":"er");'
    'e.innerHTML=t;'
    'setTimeout(function(){e.innerHTML="";e.className=""},4000)'
    '}'

    'function tab(n){'
    'document.querySelectorAll(".tab").forEach(function(e){e.classList.remove("active")});'
    'document.querySelectorAll(".tc").forEach(function(e){e.classList.remove("active")});'
    'document.querySelector(".tab[onclick=\'tab(\\\'"+ n +"\\\')\'"]").classList.add("active");'
    'document.getElementById("t-"+n).classList.add("active")'
    '}'

    'async function sha256hex(s){'
    'var buf=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s));'
    'return Array.from(new Uint8Array(buf))'
    '.map(function(b){return b.toString(16).padStart(2,"0")}).join("")'
    '}'

    'async function doLogin(){'
    'var u=document.getElementById("lu").value.trim();'
    'var p=document.getElementById("lp").value;'
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

    'function loadAll(){'
    'pollStatus();'
    'loadDay(document.getElementById("dsel").value||"Monday");'
    'loadCfg()'
    '}'

    'function pollStatus(){'
    'fetch("/status",{headers:H()})'
    '.then(function(r){if(r.status===401){logout();return null}return r.json()})'
    '.then(function(d){'
    'if(!d||!d.time)return;'
    'document.getElementById("ct").textContent=d.time||"--";'
    'document.getElementById("cd").textContent=d.day||"--";'
    'document.getElementById("ns").textContent=d.ntp_last_sync||"--";'
    'var nb=d.next_bell;'
    'document.getElementById("nb").textContent='
    'nb?(nb.day+" "+nb.event.time+" "+nb.event.event_name):"None"'
    '}).catch(function(){})'
    '}'

    'function logout(){'
    'tok=null;sessionStorage.removeItem("tok");'
    'document.getElementById("lo").style.display="flex"'
    '}'

    'function loadDay(day){'
    'fetch("/schedule?day="+day,{headers:H()})'
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
    '"\\"\\",\\""+e.time+"\\"\')>Del</button></td>"'
    '+"</tr>"'
    '});'
    'document.getElementById("sb").innerHTML='
    'h||"<tr><td colspan=5 style=text-align:center>No events</td></tr>"'
    '})'
    '}'

    'function del(day,time){'
    'if(!confirm("Delete "+day+" "+time+"?"))return;'
    'fetch("/schedule/delete",{method:"POST",headers:H(),'
    'body:JSON.stringify({day:day,time:time})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ms",d.message,d.success);loadDay(day)})'
    '}'

    # FIX 10: .replace(":","") converts "HH:MM" from <input type="time"> to "HHMM"
    'function addEvent(){'
    'var tv=document.getElementById("at").value;'
    'var d={'
    'day:document.getElementById("ad").value,'
    'time:tv,'
    'event_name:document.getElementById("an").value,'
    'bell_pattern:document.getElementById("ap").value,'
    'duration_seconds:parseInt(document.getElementById("adur").value)'
    '};'
    'if(!d.time||!d.event_name){msg("ma","Fill all fields",false);return}'
    'fetch("/schedule/add",{method:"POST",headers:H(),body:JSON.stringify(d)})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ma",d.message,d.success)})'
    '}'

    'function uploadSched(){'
    'var j=document.getElementById("uj").value;'
    'try{JSON.parse(j)}catch(e){msg("ma","Invalid JSON: "+e,false);return}'
    'fetch("/schedule/upload",{method:"POST",headers:H(),body:j})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("ma",d.message,d.success)})'
    '}'

    'function ringBell(){'
    'fetch("/bell/ring",{method:"POST",headers:H(),'
    'body:JSON.stringify({'
    'pattern:document.getElementById("bp").value,'
    'duration:parseInt(document.getElementById("bd").value)'
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

    'function saveCfg(){'
    'var d={'
    'auth_user:document.getElementById("cu").value,'
    'auth_pass:document.getElementById("cp").value,'
    'ntp_host:document.getElementById("nh").value,'
    'ntp_interval_hours:parseInt(document.getElementById("ni").value)||1,'
    'wifi_ssid:document.getElementById("ws").value,'
    'wifi_password:document.getElementById("wp").value,'
    'allowed_cidr:document.getElementById("ac").value'
    '};'
    'fetch("/config/update",{method:"POST",headers:H(),body:JSON.stringify(d)})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mc",d.message,d.success)})'
    '}'

    'function dlBackup(){window.open("/config/backup","_blank")}'

    'function addHoliday(){'
    'var d=document.getElementById("hd").value;'
    'if(!d){msg("mc","Select a date",false);return}'
    'fetch("/schedule/holiday",{method:"POST",headers:H(),'
    'body:JSON.stringify({date:d})})'
    '.then(function(r){return r.json()})'
    '.then(function(d){msg("mc",d.message,d.success)})'
    '}'

    'function loadLogs(){'
    'fetch("/logs",{headers:H()})'
    '.then(function(r){return r.text()})'
    '.then(function(t){document.getElementById("lc").textContent=t||"no logs yet"})'
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
    '.then(function(r){'
    'if(r.status===401)logout();'
    'else{document.getElementById("lo").style.display="none";loadAll()}'
    '})'
    '.catch(function(){logout()})'
    '});'

    'setInterval(pollStatus,5000);'

    '</script></body></html>'
)


class WebServer:
    def __init__(self, cfg, scheduler, bell, ntp, wifi):
        self.cfg     = cfg
        self.sched   = scheduler
        self.bell    = bell
        self.ntp     = ntp
        self.wifi    = wifi
        self.sock    = None
        self.running = False
        self.nonce   = _boot_nonce()   # rotates every reboot
        self.token   = None            # None until first successful login

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        port = self.cfg.get('web_port', 80)
        try:
            self.sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
            self.sock.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
            self.sock.bind(('0.0.0.0', port))
            self.sock.listen(2)
            self.sock.setblocking(False)
            self.running = True
            print(f"WEB  Dashboard  http://{self.wifi.get_ip()}:{port}")
        except Exception as e:
            print(f"WEB  Start failed: {e}")

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.running = False

    # ── IP ACL (FIX 9) ────────────────────────────────────────────────────────
    def _ip_allowed(self, ip: str) -> bool:
        cidr = self.cfg.get('allowed_cidr', '0.0.0.0/0')
        return _cidr_allows(ip, cidr)

    # ── Cooperative poll ──────────────────────────────────────────────────────
    def poll(self):
        if not self.sock:
            return
        try:
            conn, addr = self.sock.accept()
        except OSError:
            return   # EAGAIN — no pending connection

        remote_ip = addr[0]
        if not self._ip_allowed(remote_ip):
            print(f"WEB  Blocked {remote_ip} (outside allowed CIDR).")
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

            body_bytes = bytes(buf[header_end + 4:])
            clen = 0
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
            print(f"WEB  Request error: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            gc.collect()

    # ── Token auth ────────────────────────────────────────────────────────────
    def _auth_ok(self, headers: str) -> bool:
        if self.token is None:
            return False
        for line in headers.split('\r\n'):
            if line.lower().startswith('x-auth-token'):
                return line.split(':', 1)[1].strip() == self.token
        return False

    # ── Response helpers (FIX 8: CRLF throughout) ────────────────────────────
    def _send_401(self, conn):
        body = b'{"error":"Unauthorized"}'
        conn.send(
            b'HTTP/1.1 401 Unauthorized\r\n'
            b'Content-Type: application/json\r\n'
            b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n'
        )
        conn.send(body)

    def _send_json(self, conn, data, status: int = 200):
        body   = ujson.dumps(data).encode()
        reason = _REASON.get(status, 'OK')   # FIX 7
        conn.send(
            (
                f'HTTP/1.1 {status} {reason}\r\n'
                f'Content-Type: application/json\r\n'
                f'Content-Length: {len(body)}\r\n'
                f'Access-Control-Allow-Origin: *\r\n\r\n'
            ).encode()
        )
        conn.send(body)

    def _send_text(self, conn, text: str):
        b = text.encode()
        conn.send(
            (
                f'HTTP/1.1 200 OK\r\n'
                f'Content-Type: text/plain\r\n'
                f'Content-Length: {len(b)}\r\n\r\n'
            ).encode()
        )
        conn.send(b)

    def _send_html(self, conn, html: str):
        b = html.encode('utf-8')
        conn.send(
            (
                f'HTTP/1.1 200 OK\r\n'
                f'Content-Type: text/html; charset=utf-8\r\n'
                f'Content-Length: {len(b)}\r\n\r\n'
            ).encode()
        )
        conn.send(b)

    # ── Dispatcher ────────────────────────────────────────────────────────────
    def _dispatch(self, conn, method: str, fullpath: str, headers: str, body: str):
        path  = fullpath.split('?')[0]
        query = fullpath.split('?')[1] if '?' in fullpath else ''

        # ── Public endpoints ─────────────────────────────────────────────────
        if method == 'GET' and path == '/':
            html = _DASHBOARD_HTML.replace('LOGINOVERLAY', _LOGIN_OVERLAY)
            self._send_html(conn, html)
            return

        if method == 'GET' and path == '/nonce':
            self._send_json(conn, {'nonce': self.nonce})
            return

        if method == 'POST' and path == '/login':
            try:
                d    = ujson.loads(body)
                user = self.cfg.get('auth_user', 'admin')
                pw   = self.cfg.get('auth_pass', 'admin123')
                pw_h = _sha256hex(pw)
                exp  = _sha256hex(f'{user}{pw_h}{self.nonce}')
                if d.get('user') == user and d.get('token') == exp:
                    self.token = d['token']
                    self._send_json(conn, {'ok': True})
                else:
                    self._send_json(conn, {'ok': False}, status=401)
            except Exception as e:
                self._send_json(conn, {'ok': False, 'error': str(e)}, status=400)
            return

        # ── All other endpoints require valid token ───────────────────────────
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
                'ntp_synced':    self.ntp.is_synced(),
                'ntp_last_sync': self.ntp.last_sync_str(),
                'ip':            self.wifi.get_ip(),
                'next_bell':     self.sched.get_next_event(),
            })

        elif method == 'GET' and path == '/schedule':
            day = next(
                (p.split('=', 1)[1] for p in query.split('&') if p.startswith('day=')),
                None
            )
            if day:
                self._send_json(conn, {
                    'day':    day,
                    'events': self.sched.get_day_schedule(day)
                })
            else:
                self._send_json(conn, {'schedule': self.sched.get_schedule()})

        elif method == 'GET' and path == '/config':
            cfg = self.cfg.get_all()
            cfg.pop('auth_pass', None)
            self._send_json(conn, cfg)

        elif method == 'GET' and path == '/config/backup':
            backup = {
                'config':   self.cfg.get_all(),
                'schedule': self.sched.get_schedule()
            }
            backup['config'].pop('auth_pass', None)
            b = ujson.dumps(backup).encode()
            conn.send(
                (
                    f'HTTP/1.1 200 OK\r\n'
                    f'Content-Type: application/json\r\n'
                    f'Content-Disposition: attachment; filename=bellbackup.json\r\n'
                    f'Content-Length: {len(b)}\r\n\r\n'
                ).encode()
            )
            conn.send(b)

        elif method == 'GET' and path == '/logs':
            self._send_text(conn, '\n'.join(self.sched.get_logs(100)))

        elif method == 'POST' and path == '/schedule/add':
            try:
                d     = ujson.loads(body)
                ok, m = self.sched.add_event(
                    d['day'], d['time'], d['event_name'],
                    d.get('bell_pattern', 'single_ring'),
                    d.get('duration_seconds', 3)
                )
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/edit':
            try:
                d     = ujson.loads(body)
                ok, m = self.sched.edit_event(d['day'], d['time'], d)
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/delete':
            try:
                d     = ujson.loads(body)
                ok, m = self.sched.delete_event(d['day'], d['time'])
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/upload':
            try:
                ok, m = self.sched.upload_schedule(body)
                self._send_json(conn, {'success': ok, 'message': m})
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/schedule/holiday':
            try:
                d  = ujson.loads(body)
                ok = self.sched.add_holiday(d['date'])
                self._send_json(conn, {
                    'success': ok,
                    'message': 'Holiday marked.' if ok else 'Already marked.'
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/bell/ring':
            try:
                d = ujson.loads(body)
                self.bell.ring(d.get('pattern', 'single_ring'), d.get('duration', 3))
                self._send_json(conn, {
                    'success': True,
                    'message': f"Rang {d.get('pattern')} for {d.get('duration')}s"
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        elif method == 'POST' and path == '/config/update':
            try:
                d = ujson.loads(body)
                for k in ('auth_user', 'auth_pass', 'ntp_host',
                          'ntp_interval_hours', 'allowed_cidr'):
                    if k in d and d[k]:
                        self.cfg.update(k, d[k])
                if d.get('wifi_ssid'):
                    self.cfg.update_wifi(d['wifi_ssid'], d.get('wifi_password', ''))
                self.token = None
                self.nonce = _boot_nonce()
                self._send_json(conn, {
                    'success': True,
                    'message': 'Config saved. Session invalidated — please log in again.'
                })
            except Exception as e:
                self._send_json(conn, {'success': False, 'message': str(e)})

        else:
            conn.send(b'HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found')

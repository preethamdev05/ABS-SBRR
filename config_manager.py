# config_manager.py  v2.2.0
# FIX 6a: default_cfg() allowed_cidr = '0.0.0.0/0'  (was '192.168.1.0/24')
# FIX 6b: get_wifi() confirmed present and correct — used by wifi_manager.py

import ujson
import machine
import uhashlib
import ubinascii

try:
    import ucryptolib
    _AES_OK = True
except ImportError:
    _AES_OK = False
    print("CONFIG ucryptolib unavailable — credentials stored plaintext.")

_CONFIG_FILE   = 'config.json'
_WIFI_FILE     = 'wifi.json'
_SALT          = b'SBRRBellv22026'
_ENC_PREFIX    = 'enc:'
_SENSITIVE_CFG  = frozenset(['auth_pass'])
_SENSITIVE_WIFI = frozenset(['password'])


# ── Key derivation ────────────────────────────────────────────────────────────
def _derive_key() -> bytes:
    uid = machine.unique_id()   # 8 bytes on RP2350 — device-unique
    h   = uhashlib.sha256(uid + _SALT)
    return h.digest()[:16]      # AES-128: 128-bit key

def _derive_iv(key: bytes) -> bytes:
    h = uhashlib.sha256(key + b'ivbellv2')
    return h.digest()[:16]


# ── PKCS7 padding ─────────────────────────────────────────────────────────────
def _pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n] * n)

def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if n < 1 or n > 16:
        return data
    return data[:-n]


# ── Field-level AES-128-CBC encrypt / decrypt ─────────────────────────────────
def _encrypt(value: str) -> str:
    if not _AES_OK or not value:
        return value
    try:
        key    = _derive_key()
        iv     = _derive_iv(key)
        cipher = ucryptolib.aes(key, 2, iv)   # mode 2 = CBC
        ct     = cipher.encrypt(_pad(value.encode()))
        return _ENC_PREFIX + ubinascii.b2a_base64(ct).decode().strip()
    except Exception as e:
        print(f"CONFIG Encrypt error: {e}")
        return value

def _decrypt(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(_ENC_PREFIX):
        return value    # plaintext: legacy install or AES unavailable
    if not _AES_OK:
        return value
    try:
        key    = _derive_key()
        iv     = _derive_iv(key)
        cipher = ucryptolib.aes(key, 2, iv)
        ct     = ubinascii.a2b_base64(value[len(_ENC_PREFIX):])
        return _unpad(cipher.decrypt(ct)).decode()
    except Exception as e:
        print(f"CONFIG Decrypt error: {e}")
        return value   # fallback — handles migration edge case


# ── ConfigManager ─────────────────────────────────────────────────────────────
class ConfigManager:
    def __init__(self):
        self.cfg  = {}
        self.wifi = {}
        self.load()

    @staticmethod
    def _default_cfg() -> dict:
        return {
            'bell_pin': 15, 'led_pin': 25, 'button_pin': 14,
            'i2c_sda': 4,   'i2c_scl': 5,
            'ntp_host': 'pool.ntp.org', 'ntp_interval_hours': 1,
            'timezone_offset': 19800,
            'auth_user': 'admin', 'auth_pass': 'admin123',
            'web_port': 80, 'watchdog_timeout_ms': 8388,
            'allowed_cidr': '0.0.0.0/0',   # FIX 6a: allow all — works in Wokwi + physical
        }

    @staticmethod
    def _default_wifi() -> dict:
        return {
            'ssid': '', 'password': '',
            'ap_ssid': 'SBRRBellAP', 'ap_password': 'bellsystem',
        }

    def load(self):
        for attr, fname, default, sensitive in [
            ('cfg',  _CONFIG_FILE, self._default_cfg(),  _SENSITIVE_CFG),
            ('wifi', _WIFI_FILE,   self._default_wifi(), _SENSITIVE_WIFI),
        ]:
            try:
                with open(fname, 'r') as f:
                    raw = ujson.load(f)
                for k in sensitive:
                    if k in raw:
                        raw[k] = _decrypt(raw[k])
                setattr(self, attr, raw)
            except Exception as e:
                print(f"CONFIG {fname} load error: {e}. Using defaults.")
                setattr(self, attr, default)

    def save(self):
        try:
            out = dict(self.cfg)
            for k in _SENSITIVE_CFG:
                if k in out:
                    out[k] = _encrypt(out[k])
            with open(_CONFIG_FILE, 'w') as f:
                ujson.dump(out, f)
        except Exception as e:
            print(f"CONFIG Save error: {e}")

    def save_wifi(self):
        try:
            out = dict(self.wifi)
            for k in _SENSITIVE_WIFI:
                if k in out:
                    out[k] = _encrypt(out[k])
            with open(_WIFI_FILE, 'w') as f:
                ujson.dump(out, f)
        except Exception as e:
            print(f"CONFIG WiFi save error: {e}")

    # ── Accessors (always return plaintext) ───────────────────────────────────
    def get(self, key, default=None):
        """Read from config.json dict."""
        return self.cfg.get(key, default)

    def get_wifi(self, key, default=None):
        """Read from wifi.json dict. FIX 6b: used by wifi_manager.py for all WiFi keys."""
        return self.wifi.get(key, default)

    def get_all(self) -> dict:
        """Return full cfg dict (plaintext). Caller must pop auth_pass before sending over API."""
        return dict(self.cfg)

    def update(self, key, value):
        """Store plaintext in memory; encrypt on disk write."""
        self.cfg[key] = value
        self.save()

    def update_wifi(self, ssid: str, password: str):
        self.wifi['ssid']     = ssid
        self.wifi['password'] = password
        self.save_wifi()

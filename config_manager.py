# config_manager.py  v2.0.0
# Production: AES-128-CBC field encryption for sensitive credentials.
# Gracefully degrades to plaintext when ucryptolib is absent (Wokwi sim).
# All accessors always return plaintext; encryption is transparent to callers.

import ujson
import machine
import uhashlib
import ubinascii

try:
    import ucryptolib
    _AES_AVAILABLE = True
except ImportError:
    _AES_AVAILABLE = False

_CONFIG_FILE    = 'config.json'
_WIFI_FILE      = 'wifi.json'
_SALT           = b'SBRRBellV1Prod2026'
_ENC_PREFIX     = 'enc:'
_SENSITIVE_CFG  = frozenset(['auth_pass'])
_SENSITIVE_WIFI = frozenset(['password'])


# ── Key derivation (device-unique) ───────────────────────────────────────────

def _derive_key() -> bytes:
    uid = machine.unique_id()
    return uhashlib.sha256(uid + _SALT).digest()[:16]


def _derive_iv(key: bytes) -> bytes:
    return uhashlib.sha256(key + b'ivv1prod').digest()[:16]


# ── PKCS7 padding ────────────────────────────────────────────────────────────

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


# ── AES-128-CBC encrypt / decrypt ────────────────────────────────────────────

def _encrypt(value: str) -> str:
    if not _AES_AVAILABLE or not value:
        return value
    try:
        key    = _derive_key()
        iv     = _derive_iv(key)
        cipher = ucryptolib.aes(key, 2, iv)
        ct     = cipher.encrypt(_pad(value.encode()))
        return _ENC_PREFIX + ubinascii.b2a_base64(ct).decode().strip()
    except Exception as e:
        print(f'CONFIG encrypt error: {e}')
        return value


def _decrypt(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(_ENC_PREFIX):
        return value
    if not _AES_AVAILABLE:
        return value
    try:
        key    = _derive_key()
        iv     = _derive_iv(key)
        cipher = ucryptolib.aes(key, 2, iv)
        ct     = ubinascii.a2b_base64(value[len(_ENC_PREFIX):])
        return _unpad(cipher.decrypt(ct)).decode()
    except Exception as e:
        print(f'CONFIG decrypt error: {e}')
        return value


# ── ConfigManager ─────────────────────────────────────────────────────────────

class ConfigManager:
    def __init__(self):
        self.cfg  = {}
        self.wifi = {}
        self.load()

    # ── Defaults ─────────────────────────────────────────────────────────────

    @staticmethod
    def _default_cfg() -> dict:
        return {
            'bell_pin':            15,
            'led_pin':             25,
            'button_pin':          14,
            'i2c_sda':             4,
            'i2c_scl':             5,
            'rtc_enabled':         True,
            'ntp_host':            'pool.ntp.org',
            'ntp_interval_hours':  1,
            'timezone_offset':     19800,
            'auth_user':           'admin',
            'auth_pass':           'admin123',
            'web_port':            80,
            'watchdog_timeout_ms': 8388,
            'allowed_cidr':        '192.168.4.0/24',
            'ntp_use_http':        True,
            'max_bell_duration':   30,
            'token_ttl_seconds':   86400,
            'login_max_attempts':  5,
            'login_lockout_secs':  60,
        }

    @staticmethod
    def _default_wifi() -> dict:
        return {
            'ssid':        '',
            'password':    '',
            'ap_ssid':     'SBRRBell_AP',
            'ap_password': '',   # generated at first boot
        }

    # ── Load / Save ──────────────────────────────────────────────────────────

    def load(self):
        sources = [
            ('cfg',  _CONFIG_FILE, self._default_cfg(),  _SENSITIVE_CFG),
            ('wifi', _WIFI_FILE,   self._default_wifi(), _SENSITIVE_WIFI),
        ]
        for attr, fname, default, sensitive in sources:
            try:
                with open(fname, 'r') as f:
                    raw = ujson.load(f)
                for k in sensitive:
                    if k in raw:
                        raw[k] = _decrypt(raw[k])
                # Merge with defaults so new keys are always present
                merged = dict(default)
                merged.update(raw)
                setattr(self, attr, merged)
            except Exception as e:
                print(f'CONFIG load {fname}: {e} — using defaults')
                setattr(self, attr, dict(default))

    def save(self):
        try:
            out = dict(self.cfg)
            for k in _SENSITIVE_CFG:
                if k in out and out[k]:
                    out[k] = _encrypt(out[k])
            with open(_CONFIG_FILE, 'w') as f:
                ujson.dump(out, f)
        except Exception as e:
            print(f'CONFIG save error: {e}')

    def save_wifi(self):
        try:
            out = dict(self.wifi)
            for k in _SENSITIVE_WIFI:
                if k in out and out[k]:
                    out[k] = _encrypt(out[k])
            with open(_WIFI_FILE, 'w') as f:
                ujson.dump(out, f)
        except Exception as e:
            print(f'CONFIG wifi save error: {e}')

    # ── Accessors (always return plaintext) ──────────────────────────────────

    def get(self, key, default=None):
        return self.cfg.get(key, default)

    def get_wifi(self, key, default=None):
        return self.wifi.get(key, default)

    def get_all(self) -> dict:
        """Returns full cfg dict in plaintext. Caller must strip auth_pass before API responses."""
        return dict(self.cfg)

    def get_public(self) -> dict:
        """Returns config dict with all sensitive fields stripped."""
        out = dict(self.cfg)
        out.pop('auth_pass', None)
        return out

    # ── Mutators ─────────────────────────────────────────────────────────────

    def update(self, key, value):
        self.cfg[key] = value
        self.save()

    def update_many(self, updates: dict):
        """Batch update — single save call."""
        self.cfg.update(updates)
        self.save()

    def update_wifi(self, ssid: str, password: str):
        self.wifi['ssid']     = ssid
        self.wifi['password'] = password
        self.save_wifi()

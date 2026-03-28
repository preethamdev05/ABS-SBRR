# rtc_sync.py  v1.0.0
# DS3231 RTC driver for Raspberry Pi Pico W (MicroPython).
# I2C address: 0x68 (fixed).
#
# Features:
#   - Read time (BCD → decimal, returns utime.localtime-compatible tuple)
#   - Write time (decimal → BCD)
#   - Detect power-fail flag (OSF bit in status register)
#   - Temperature read (integer °C — DS3231 only)
#
# Usage:
#   rtc = RTCSync(cfg)              # uses i2c_sda / i2c_scl from config
#   if rtc.is_available():
#       t = rtc.read_time()         # (year, month, day, hour, min, sec, ...)
#       rtc.write_time_from_epoch(epoch, tz_offset)
#       rtc.apply_to_machine_rtc()  # sets machine.RTC()

import machine
import utime
from micropython import const

_DS3231_ADDR       = const(0x68)
_REG_TIME          = const(0x00)
_REG_STATUS        = const(0x0F)
_REG_CONTROL       = const(0x0E)
_REG_TEMP_MSB      = const(0x11)


def _bcd_to_dec(bcd):
    return (bcd >> 4) * 10 + (bcd & 0x0F)


def _dec_to_bcd(dec):
    return ((dec // 10) << 4) | (dec % 10)


class RTCSync:
    def __init__(self, cfg):
        self._cfg   = cfg
        self._i2c   = None
        self._avail = False
        self._init_i2c()

    def _init_i2c(self):
        sda_pin = self._cfg.get('i2c_sda', 4)
        scl_pin = self._cfg.get('i2c_scl', 5)
        try:
            i2c = machine.I2C(0, sda=machine.Pin(sda_pin),
                              scl=machine.Pin(scl_pin), freq=400000)
            devices = i2c.scan()
            if _DS3231_ADDR in devices:
                self._i2c   = i2c
                self._avail = True
                print(f'RTC DS3231 found on I2C (SDA={sda_pin}, SCL={scl_pin})')
                # Ensure control register is sane: EOSC=0, INTCN=1, BBSQW=0
                self._write_reg(_REG_CONTROL, b'\x04')
            else:
                print(f'RTC DS3231 not found at 0x{_DS3231_ADDR:02X}. '
                      f'Devices: {[hex(a) for a in devices]}')
        except Exception as e:
            print(f'RTC I2C init failed: {e}')

    # ── Low-level I2C helpers ─────────────────────────────────────────────────

    def _read_regs(self, reg, count):
        return self._i2c.readfrom_mem(_DS3231_ADDR, reg, count)

    def _write_reg(self, reg, data):
        self._i2c.writeto_mem(_DS3231_ADDR, reg, data)

    # ── Read time from DS3231 ────────────────────────────────────────────────

    def read_time(self):
        """
        Returns (year, month, day, hour, minute, second, weekday, yearday)
        weekday: 0=Mon … 6=Sun (matches utime.localtime convention)
        """
        raw = self._read_regs(_REG_TIME, 7)
        sec   = _bcd_to_dec(raw[0] & 0x7F)
        minute = _bcd_to_dec(raw[1] & 0x7F)
        # Handle 12/24h mode (bit 6 of hours register)
        if raw[2] & 0x40:  # 12-hour mode
            hour = _bcd_to_dec(raw[2] & 0x1F)
            if raw[2] & 0x20:  # PM
                hour += 12
            if hour == 24:
                hour = 0
        else:  # 24-hour mode
            hour = _bcd_to_dec(raw[2] & 0x3F)
        day    = _bcd_to_dec(raw[4] & 0x3F)
        month  = _bcd_to_dec(raw[5] & 0x1F)
        year   = _bcd_to_dec(raw[6]) + 2000
        # DS3231 weekday: 1=Sun … 7=Sat → convert to 0=Mon … 6=Sun
        rtc_wday = _bcd_to_dec(raw[3])
        # Map: DS3231 Sun=1 → utime Sun=6; DS3231 Mon=2 → utime Mon=0
        wday = (rtc_wday + 5) % 7
        return (year, month, day, hour, minute, sec, wday, 0)

    # ── Write time to DS3231 ─────────────────────────────────────────────────

    def write_time(self, year, month, day, hour, minute, second):
        """Write time in 24-hour mode."""
        # weekday: 1=Sun … 7=Sat (DS3231 convention)
        # We calculate from utime convention (0=Mon … 6=Sun)
        # Zeller-ish: just use a simple lookup
        # For simplicity, derive from a known reference or accept the input
        buf = bytearray(7)
        buf[0] = _dec_to_bcd(second)
        buf[1] = _dec_to_bcd(minute)
        buf[2] = _dec_to_bcd(hour)       # bit 6=0 → 24h mode
        # Calculate DS3231 weekday (1=Sun … 7=Sat)
        # Use Tomohiko Sakamoto or just compute from date
        utime_tuple = utime.localtime(utime.mktime((year, month, day, hour, minute, second, 0, 0)))
        utime_wday = utime_tuple[6]  # 0=Mon … 6=Sun
        ds_wday = (utime_wday + 1) % 7 + 1  # → 1=Sun … 7=Sat
        buf[3] = _dec_to_bcd(ds_wday)
        buf[4] = _dec_to_bcd(day)
        buf[5] = _dec_to_bcd(month)
        buf[6] = _dec_to_bcd(year - 2000)
        # Clear OSF flag (bit 7 of status register) after setting time
        self._write_reg(_REG_TIME, buf)
        status = self._read_regs(_REG_STATUS, 1)[0]
        self._write_reg(_REG_STATUS, bytes([status & 0x7F]))
        print(f'RTC Written {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}')

    def write_time_from_epoch(self, unix_epoch, tz_offset=0):
        """Write time from Unix epoch (UTC) + timezone offset (seconds)."""
        local_epoch = unix_epoch + tz_offset
        t = utime.localtime(local_epoch)
        self.write_time(t[0], t[1], t[2], t[3], t[4], t[5])

    # ── Apply RTC time to machine.RTC() ──────────────────────────────────────

    def apply_to_machine_rtc(self):
        """Read DS3231 and set machine.RTC(). Returns True on success."""
        if not self._avail:
            return False
        try:
            t = self.read_time()
            # machine.RTC().datetime() expects (year, month, day, weekday, hour, min, sec, subseconds)
            # weekday: 1=Mon … 7=Sun
            m_wday = t[6] + 1  # utime 0=Mon → machine 1=Mon
            machine.RTC().datetime((t[0], t[1], t[2], m_wday, t[3], t[4], t[5], 0))
            print(f'RTC Applied DS3231 → machine.RTC: '
                  f'{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}')
            return True
        except Exception as e:
            print(f'RTC apply_to_machine_rtc failed: {e}')
            return False

    # ── Power-fail detection ──────────────────────────────────────────────────

    def has_power_fail(self):
        """Returns True if OSF bit is set (oscillator stopped = time unreliable)."""
        if not self._avail:
            return True
        try:
            status = self._read_regs(_REG_STATUS, 1)[0]
            return bool(status & 0x80)
        except Exception:
            return True

    # ── Temperature (DS3231 only) ────────────────────────────────────────────

    def read_temperature(self):
        """Returns temperature in °C as integer (DS3231 built-in sensor)."""
        if not self._avail:
            return None
        try:
            msb = self._read_regs(_REG_TEMP_MSB, 1)[0]
            lsb_reg = self._read_regs(_REG_TEMP_MSB + 1, 1)[0]
            frac = (lsb_reg >> 6) * 25  # 0, 25, 50, 75 → hundredths
            # Handle signed (bit 7 of msb)
            if msb & 0x80:
                msb = msb - 256
            return msb
        except Exception:
            return None

    # ── Accessors ─────────────────────────────────────────────────────────────

    def is_available(self):
        return self._avail

    def status_str(self):
        if not self._avail:
            return 'Not detected'
        pf = 'POWER FAIL' if self.has_power_fail() else 'OK'
        temp = self.read_temperature()
        temp_str = f', {temp}°C' if temp is not None else ''
        return f'DS3231 {pf}{temp_str}'

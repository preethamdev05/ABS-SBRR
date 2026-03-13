# schedule_manager.py  v1.0.0
# Production: Full CRUD schedule management with holiday override,
# next-event lookup, append-mode logging, and size-based log rotation.

import ujson
import utime
import uos

SCHEDULE_FILE = 'schedule.json'
LOG_FILE      = 'logs.txt'
LOG_FILE_OLD  = 'logs_old.txt'
MAX_LOG_BYTES = 32768   # 32 KiB — rotate when exceeded

_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


class ScheduleManager:
    def __init__(self, cfg):
        self._cfg      = cfg
        self._schedule = {}   # { 'Monday': [{time, event_name, bell_pattern, duration_seconds}] }
        self._holidays = []   # ['YYYY-MM-DD', …]
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self):
        try:
            with open(SCHEDULE_FILE, 'r') as f:
                raw = ujson.load(f)
            self._schedule = raw.get('schedule', raw if isinstance(raw, dict) else {})
            self._holidays = raw.get('holidays', [])
            total = sum(len(v) for v in self._schedule.values())
            print(f'SCHED Loaded {total} events across {len(self._schedule)} day(s).')
        except Exception as e:
            print(f'SCHED Load error: {e} — starting empty.')
            self._schedule = {}
            self._holidays = []

    def save(self):
        try:
            with open(SCHEDULE_FILE, 'w') as f:
                ujson.dump({'schedule': self._schedule, 'holidays': self._holidays}, f)
        except Exception as e:
            print(f'SCHED Save error: {e}')

    # ── Cooperative tick (satisfies dispatcher interface) ─────────────────────

    def tick(self):
        pass

    # ── Event queries ─────────────────────────────────────────────────────────

    def get_event(self, day: str, hhmm: str):
        """Return event dict if day is not a holiday and a matching time entry exists."""
        if self._today_str() in self._holidays:
            return None
        for ev in self._schedule.get(day, []):
            if ev.get('time') == hhmm:
                return ev
        return None

    def get_schedule(self) -> dict:
        return self._schedule

    def get_day_schedule(self, day: str) -> list:
        return self._schedule.get(day, [])

    def get_next_event(self):
        """Return the next upcoming event within the next 7 days, or None."""
        now = utime.localtime()
        cur = f'{now[3]:02d}:{now[4]:02d}'
        idx = now[6]   # 0=Mon … 6=Sun
        for offset in range(7):
            day = _DAYS[(idx + offset) % 7]
            events = sorted(self._schedule.get(day, []), key=lambda x: x['time'])
            for ev in events:
                if offset == 0 and ev['time'] <= cur:
                    continue
                return {'day': day, 'event': ev}
        return None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_event(self, day: str, time: str, event_name: str,
                  bell_pattern: str, duration_seconds) -> tuple:
        if day not in _DAYS:
            return False, f'Invalid day: {day}'
        if not _valid_hhmm(time):
            return False, f'Invalid time format: {time}'
        if not event_name or not event_name.strip():
            return False, 'Event name must not be empty'

        if day not in self._schedule:
            self._schedule[day] = []

        for ev in self._schedule[day]:
            if ev['time'] == time:
                return False, 'An event already exists at this time.'

        self._schedule[day].append({
            'time':             time,
            'event_name':       event_name.strip(),
            'bell_pattern':     bell_pattern,
            'duration_seconds': int(duration_seconds),
        })
        self._schedule[day].sort(key=lambda x: x['time'])
        self.save()
        return True, 'Event added.'

    def edit_event(self, day: str, time: str, new_data: dict) -> tuple:
        for ev in self._schedule.get(day, []):
            if ev['time'] == time:
                allowed = ('event_name', 'bell_pattern', 'duration_seconds')
                ev.update({k: new_data[k] for k in allowed if k in new_data})
                self.save()
                return True, 'Event updated.'
        return False, 'Event not found.'

    def delete_event(self, day: str, time: str) -> tuple:
        before = len(self._schedule.get(day, []))
        self._schedule[day] = [
            e for e in self._schedule.get(day, []) if e['time'] != time
        ]
        if len(self._schedule.get(day, [])) < before:
            self.save()
            return True, 'Event deleted.'
        return False, 'Event not found.'

    def upload_schedule(self, raw) -> tuple:
        try:
            data = ujson.loads(raw) if isinstance(raw, str) else raw
            self._schedule = data.get('schedule', data if isinstance(data, dict) else {})
            self._holidays = data.get('holidays', [])
            self.save()
            return True, 'Schedule uploaded successfully.'
        except Exception as e:
            return False, str(e)

    # ── Holiday management ────────────────────────────────────────────────────

    def add_holiday(self, date_str: str) -> bool:
        if date_str not in self._holidays:
            self._holidays.append(date_str)
            self.save()
            return True
        return False

    def remove_holiday(self, date_str: str) -> bool:
        if date_str in self._holidays:
            self._holidays.remove(date_str)
            self.save()
            return True
        return False

    # ── Append-mode logging with size-based rotation ─────────────────────────

    def log_event(self, day: str, time: str, name: str):
        t    = utime.localtime()
        line = (f'{t[0]}-{t[1]:02d}-{t[2]:02d} '
                f'{t[3]:02d}:{t[4]:02d}:{t[5]:02d} | {day} {time} | {name}\n')
        try:
            try:
                if uos.stat(LOG_FILE)[6] >= MAX_LOG_BYTES:
                    try:
                        uos.remove(LOG_FILE_OLD)
                    except OSError:
                        pass
                    uos.rename(LOG_FILE, LOG_FILE_OLD)
                    print('LOG Rotated logs.txt → logs_old.txt')
            except OSError:
                pass   # file absent on first write — append creates it
            with open(LOG_FILE, 'a') as f:
                f.write(line)
        except Exception as e:
            print(f'LOG Write error: {e}')

    def get_logs(self, last_n: int = 50) -> list:
        try:
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
            return lines[-last_n:] if last_n > 0 else lines
        except OSError:
            return []

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _today_str(self) -> str:
        t = utime.localtime()
        return f'{t[0]}-{t[1]:02d}-{t[2]:02d}'


def _valid_hhmm(value: str) -> bool:
    """Validate HH:MM format."""
    if len(value) != 5 or value[2] != ':':
        return False
    try:
        h = int(value[:2])
        m = int(value[3:])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False

# schedule_manager.py  v3.0.0
# Production: Full CRUD schedule management with holiday override,
# next-event lookup, append-mode logging, and size-based log rotation.
# v3: Added copy_day_schedule() for day-copy feature.

import ujson
import utime
import uos

SCHEDULE_FILE = 'schedule.json'
LOG_FILE      = 'logs.txt'
LOG_FILE_OLD  = 'logs_old.txt'
MAX_LOG_BYTES = 32768   # 32 KiB — rotate when exceeded

_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_BELL_PATTERNS = frozenset([
    'single_ring', 'double_ring', 'long_ring', 'triple_ring', 'custom_pattern'
])


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
        """Return the next upcoming event within the next 7 days, skipping holidays."""
        now = utime.localtime()
        cur = f'{now[3]:02d}:{now[4]:02d}'
        cur_date = self._today_str()
        idx = now[6]   # 0=Mon … 6=Sun
        for offset in range(7):
            day = _DAYS[(idx + offset) % 7]
            # Compute date for this offset
            if offset == 0:
                check_date = cur_date
            else:
                check_secs = utime.time() + offset * 86400
                ct = utime.localtime(check_secs)
                check_date = f'{ct[0]}-{ct[1]:02d}-{ct[2]:02d}'
            if check_date in self._holidays:
                continue
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
        if bell_pattern not in _BELL_PATTERNS:
            return False, f'Invalid bell pattern: {bell_pattern}'
        max_dur = self._cfg.get('max_bell_duration', 30)
        duration_seconds = int(duration_seconds)
        if duration_seconds < 1 or duration_seconds > max_dur:
            return False, f'Duration must be 1-{max_dur} seconds'

        if day not in self._schedule:
            self._schedule[day] = []

        for ev in self._schedule[day]:
            if ev['time'] == time:
                return False, 'An event already exists at this time.'

        self._schedule[day].append({
            'time':             time,
            'event_name':       event_name.strip(),
            'bell_pattern':     bell_pattern,
            'duration_seconds': duration_seconds,
        })
        self._schedule[day].sort(key=lambda x: x['time'])
        self.save()
        return True, 'Event added.'

    def edit_event(self, day: str, time: str, new_data: dict) -> tuple:
        max_dur = self._cfg.get('max_bell_duration', 30)
        for ev in self._schedule.get(day, []):
            if ev['time'] == time:
                allowed = ('event_name', 'bell_pattern', 'duration_seconds', 'time')
                for k in allowed:
                    if k in new_data:
                        if k == 'bell_pattern' and new_data[k] not in _BELL_PATTERNS:
                            return False, f'Invalid bell pattern: {new_data[k]}'
                        if k == 'duration_seconds':
                            d = int(new_data[k])
                            if d < 1 or d > max_dur:
                                return False, f'Duration must be 1-{max_dur} seconds'
                            new_data[k] = d
                        if k == 'time' and not _valid_hhmm(new_data[k]):
                            return False, f'Invalid time format: {new_data[k]}'
                # Check for time conflict if time is being changed
                new_time = new_data.get('time', time)
                if new_time != time:
                    for other in self._schedule.get(day, []):
                        if other is not ev and other['time'] == new_time:
                            return False, 'An event already exists at this time.'
                ev.update({k: new_data[k] for k in allowed if k in new_data})
                self._schedule[day].sort(key=lambda x: x['time'])
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
            # Validate structure
            sched = data.get('schedule', data if isinstance(data, dict) else {})
            for day, events in sched.items():
                if day not in _DAYS:
                    return False, f'Invalid day name: {day}'
                if not isinstance(events, list):
                    return False, f'Events for {day} must be a list'
                for ev in events:
                    if 'time' not in ev or 'event_name' not in ev:
                        return False, f'Missing required field in {day} event'
                    if not _valid_hhmm(ev['time']):
                        return False, f'Invalid time in {day}: {ev["time"]}'
                    bp = ev.get('bell_pattern', 'single_ring')
                    if bp not in _BELL_PATTERNS:
                        return False, f'Invalid bell pattern in {day}: {bp}'
            self._schedule = sched
            self._holidays = data.get('holidays', [])
            self.save()
            return True, 'Schedule uploaded successfully.'
        except Exception as e:
            return False, str(e)

    # ── Day copy (v3) ─────────────────────────────────────────────────────────

    def copy_day_schedule(self, source_day: str, target_day: str) -> tuple:
        """Replace target day's events with a copy of source day's events."""
        if source_day not in _DAYS:
            return False, f'Invalid source day: {source_day}'
        if target_day not in _DAYS:
            return False, f'Invalid target day: {target_day}'
        events = self._schedule.get(source_day, [])
        self._schedule[target_day] = ujson.loads(ujson.dumps(events))
        self.save()
        return True, f'Copied {len(events)} events from {source_day} to {target_day}.'

    # ── Holiday management ────────────────────────────────────────────────────

    def add_holiday(self, date_str: str) -> tuple:
        if not _valid_date(date_str):
            return False, 'Invalid date format. Use YYYY-MM-DD.'
        if date_str in self._holidays:
            return False, 'Already marked as holiday.'
        self._holidays.append(date_str)
        self.save()
        return True, 'Holiday marked — bells will be suppressed.'

    def remove_holiday(self, date_str: str) -> tuple:
        if date_str in self._holidays:
            self._holidays.remove(date_str)
            self.save()
            return True, 'Holiday removed.'
        return False, 'Date is not marked as holiday.'

    def get_holidays(self) -> list:
        return list(self._holidays)

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


def _valid_date(value: str) -> bool:
    """Validate YYYY-MM-DD format."""
    if len(value) != 10 or value[4] != '-' or value[7] != '-':
        return False
    try:
        y = int(value[:4])
        m = int(value[5:7])
        d = int(value[8:])
        return 1 <= m <= 12 and 1 <= d <= 31 and y >= 2020 and y <= 2099
    except ValueError:
        return False

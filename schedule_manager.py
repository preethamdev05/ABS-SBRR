# schedule_manager.py – v2.0.0
# Logging uses append mode to avoid full-file rewrites on every event.
# Rotation: rename logs.txt → logs_old.txt when size exceeds 32 KiB.
# tick() stub satisfies cooperative dispatcher without conditional logic.

import ujson, utime, uos

SCHEDULE_FILE = "schedule.json"
LOG_FILE      = "logs.txt"
LOG_FILE_OLD  = "logs_old.txt"
MAX_LOG_BYTES = 32768


class ScheduleManager:
    def __init__(self, cfg):
        self._cfg      = cfg
        self._schedule = {}
        self._holidays = []
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────
    def load(self):
        try:
            with open(SCHEDULE_FILE, "r") as f:
                raw = ujson.load(f)
            self._schedule = raw.get("schedule", raw)
            self._holidays = raw.get("holidays", [])
            print(f"[SCHED] Loaded {len(self._schedule)} day(s).")
        except Exception as e:
            print(f"[SCHED] Load error: {e}")
            self._schedule = {}
            self._holidays = []

    def save(self):
        try:
            with open(SCHEDULE_FILE, "w") as f:
                ujson.dump({"schedule": self._schedule,
                            "holidays": self._holidays}, f)
        except Exception as e:
            print(f"[SCHED] Save error: {e}")

    # ── Cooperative tick ──────────────────────────────────────────────────────
    def tick(self):
        pass

    # ── Schedule queries ──────────────────────────────────────────────────────
    def get_event(self, day: str, hhmm: str):
        if self._today_str() in self._holidays:
            return None
        for ev in self._schedule.get(day, []):
            if ev.get("time") == hhmm:
                return ev
        return None

    def get_schedule(self):
        return self._schedule

    def get_day_schedule(self, day: str):
        return self._schedule.get(day, [])

    def get_next_event(self):
        now  = utime.localtime()
        cur  = f"{now[3]:02d}:{now[4]:02d}"
        days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        idx  = now[6]
        for offset in range(7):
            day = days[(idx + offset) % 7]
            for ev in sorted(self._schedule.get(day, []), key=lambda x: x["time"]):
                if offset == 0 and ev["time"] <= cur:
                    continue
                return {"day": day, "event": ev}
        return None

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def add_event(self, day, time, event_name, bell_pattern, duration_seconds):
        if day not in self._schedule:
            self._schedule[day] = []
        for ev in self._schedule[day]:
            if ev["time"] == time:
                return False, "An event at this time already exists."
        self._schedule[day].append({
            "time":             time,
            "event_name":       event_name,
            "bell_pattern":     bell_pattern,
            "duration_seconds": int(duration_seconds),
        })
        self._schedule[day].sort(key=lambda x: x["time"])
        self.save()
        return True, "Event added."

    def edit_event(self, day, time, new_data: dict):
        for ev in self._schedule.get(day, []):
            if ev["time"] == time:
                ev.update({k: v for k, v in new_data.items()
                           if k in ("event_name", "bell_pattern", "duration_seconds")})
                self.save()
                return True, "Event updated."
        return False, "Event not found."

    def delete_event(self, day, time):
        before = len(self._schedule.get(day, []))
        self._schedule[day] = [
            e for e in self._schedule.get(day, []) if e["time"] != time
        ]
        if len(self._schedule.get(day, [])) < before:
            self.save()
            return True, "Event deleted."
        return False, "Event not found."

    def upload_schedule(self, raw_json):
        try:
            data = ujson.loads(raw_json) if isinstance(raw_json, str) else raw_json
            self._schedule = data.get("schedule", data)
            self._holidays = data.get("holidays", [])
            self.save()
            return True, "Schedule uploaded successfully."
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

    # ── Append-mode logging with size-based rotation ──────────────────────────
    def log_event(self, day, time, name):
        t    = utime.localtime()
        line = (f"{t[0]}-{t[1]:02d}-{t[2]:02d} "
                f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d} | {day} {time} | {name}\n")
        try:
            try:
                if uos.stat(LOG_FILE)[6] >= MAX_LOG_BYTES:
                    try:
                        uos.remove(LOG_FILE_OLD)
                    except OSError:
                        pass
                    uos.rename(LOG_FILE, LOG_FILE_OLD)
                    print("[LOG] Rotated logs.txt → logs_old.txt")
            except OSError:
                pass   # file absent on first write; append creates it

            with open(LOG_FILE, "a") as f:
                f.write(line)
        except Exception as e:
            print(f"[LOG] Write error: {e}")

    def get_logs(self, last_n: int = 50):
        try:
            with open(LOG_FILE, "r") as f:
                return f.readlines()[-last_n:]
        except OSError:
            return []

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _today_str(self) -> str:
        t = utime.localtime()
        return f"{t[0]}-{t[1]:02d}-{t[2]:02d}"

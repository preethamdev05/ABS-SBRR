# bell_controller.py  v1.0.0
# Production: Non-blocking FSM bell controller.
# FSM: IDLE → RING_START → RING_ACTIVE → RING_STOP → IDLE
# Button: IRQ_FALLING with 50 ms debounce, flag consumed in tick().
# No blocking delays anywhere in the production path.
# test_ring() is boot-only (called before WDT is armed).

import utime
from machine import Pin
from micropython import const

PATTERNS = {
    'single_ring':    [(1.0,  0.0)],
    'double_ring':    [(0.45, 0.1),  (0.45, 0.0)],
    'long_ring':      [(1.0,  0.0)],
    'triple_ring':    [(0.28, 0.08), (0.28, 0.08), (0.28, 0.0)],
    'custom_pattern': [(0.40, 0.10), (0.15, 0.10), (0.25, 0.0)],
}

_IDLE        = const(0)
_RING_START  = const(1)
_RING_ACTIVE = const(2)
_RING_STOP   = const(3)
_DEBOUNCE_MS = const(50)


class BellController:
    def __init__(self, cfg):
        self._bell = Pin(cfg.get('bell_pin',   15), Pin.OUT, value=0)
        self._led  = Pin(cfg.get('led_pin',    25), Pin.OUT, value=0)
        self._btn  = Pin(cfg.get('button_pin', 14), Pin.IN,  Pin.PULL_UP)

        self._state      = _IDLE
        self._steps      = []
        self._step_idx   = 0
        self._step_start = 0
        self._total_ms   = 0
        self._on_phase   = True

        self._irq_fired   = False
        self._last_irq_ms = 0
        self._btn.irq(trigger=Pin.IRQ_FALLING, handler=self._btn_irq)

    # ── IRQ handler — minimal; sets flag only, no allocation, no print ────────

    def _btn_irq(self, _pin):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_irq_ms) >= _DEBOUNCE_MS:
            self._last_irq_ms = now
            self._irq_fired   = True

    # ── Public: non-blocking ring request ─────────────────────────────────────

    def ring(self, pattern: str = 'single_ring', duration_seconds: int = 3):
        """Queue a ring. Ignored if a ring is already in progress."""
        if self._state != _IDLE:
            return
        self._steps    = PATTERNS.get(pattern, PATTERNS['single_ring'])
        self._step_idx = 0
        self._total_ms = max(1, duration_seconds) * 1000
        self._state    = _RING_START
        print(f'BELL ring({pattern}, {duration_seconds}s) queued')

    # ── Public: cooperative tick — call every loop cycle ─────────────────────

    def tick(self):
        # Consume debounced button IRQ flag
        if self._irq_fired:
            self._irq_fired = False
            print('BELL Manual button pressed')
            self.ring('single_ring', 3)

        now = utime.ticks_ms()

        if self._state == _IDLE:
            return

        elif self._state == _RING_START:
            self._led.on()
            self._step_idx   = 0
            self._on_phase   = True
            self._step_start = now
            self._bell.on()
            self._state = _RING_ACTIVE

        elif self._state == _RING_ACTIVE:
            if self._step_idx >= len(self._steps):
                self._state = _RING_STOP
                return

            on_f,  off_f = self._steps[self._step_idx]
            on_ms        = int(self._total_ms * on_f)
            off_ms       = int(self._total_ms * off_f)
            elapsed      = utime.ticks_diff(now, self._step_start)

            if self._on_phase:
                if elapsed >= on_ms:
                    self._bell.off()
                    if off_ms > 0:
                        self._on_phase   = False
                        self._step_start = now
                    else:
                        self._step_idx  += 1
                        self._on_phase   = True
                        self._step_start = now
                        if self._step_idx < len(self._steps):
                            self._bell.on()
                        else:
                            self._state = _RING_STOP
            else:
                if elapsed >= off_ms:
                    self._step_idx  += 1
                    self._on_phase   = True
                    self._step_start = now
                    if self._step_idx < len(self._steps):
                        self._bell.on()
                    else:
                        self._state = _RING_STOP

        elif self._state == _RING_STOP:
            self._bell.off()
            self._led.off()
            self._state = _IDLE
            print('BELL Ring complete')

    # ── Public: accessors / control ──────────────────────────────────────────

    def is_ringing(self) -> bool:
        return self._state != _IDLE

    def stop(self):
        """Immediately silence the bell and return to IDLE."""
        self._bell.off()
        self._led.off()
        self._state = _IDLE

    def test_ring(self):
        """200 ms blocking beep — boot-only, before WDT is armed."""
        self._bell.on()
        utime.sleep_ms(200)
        self._bell.off()

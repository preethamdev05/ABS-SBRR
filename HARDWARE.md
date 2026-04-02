# ABS-SBRR v3 — Complete Hardware Build Guide

> This guide walks you through every wire, every pin, and every connection.
> Read it fully before soldering anything.

---

## 1. Complete Parts List

| # | Component | Exact Part | Qty | Notes |
|---|-----------|-----------|-----|-------|
| 1 | Microcontroller | Raspberry Pi Pico 2 W (RP2350) | 1 | The **2 W** variant has WiFi. Regular Pico 2 won't work. |
| 2 | RTC Module | DS3231 breakout board (with CR2032 holder) | 1 | Comes pre-soldered on a small PCB with 4 pins |
| 3 | Coin Battery | CR2032 3V lithium | 1 | Goes into the DS3231 board's battery holder |
| 4 | MOSFET | IRLZ44N (TO-220 package) | 1 | **Must be logic-level.** IRF540N will NOT work at 3.3V. |
| 5 | Gate Resistor | 220Ω ¼W carbon film | 1 | Any color code: Red-Red-Brown-Gold |
| 6 | Flyback Diode | 1N4007 (DO-41 package) | 1 | Protects the MOSFET from voltage spikes |
| 7 | Bell | 12V DC electric bell | 1 | Any 12V bell, buzzer, or siren |
| 8 | Power Supply | 12V DC adapter (≥1A) | 1 | Powers the bell only — NOT the Pico |
| 9 | USB Cable | Micro-USB | 1 | Powers the Pico from any USB port/adapter |
| 10 | Breadboard | Half-size or full-size | 1 | For prototyping; optional if soldering directly |
| 11 | Jumper Wires | Male-to-male, male-to-female | ~15 | M-M for breadboard, M-F for Pico's header pins |
| 12 | Push Button | 6mm tactile switch | 1 | For manual bell ring |

---

## 2. Understanding the Circuit

There are **two separate circuits** that share one common ground:

```
CIRCUIT A — Logic (3.3V):
  USB 5V → Pico 2 W → GP15 output (3.3V logic) → MOSFET Gate

CIRCUIT B — Power (12V):
  12V PSU → Bell → MOSFET Drain → MOSFET Source → GND

THE KEY: The Pico's GP15 pin controls the MOSFET gate,
which switches the 12V bell circuit on/off.
The Pico never sees 12V — only 3.3V logic.
```

**The two circuits connect at exactly TWO points:**
1. GP15 → Gate resistor → MOSFET Gate (the control signal)
2. Pico GND → 12V PSU GND → MOSFET Source (shared ground)

---

## 3. The MOSFET — IRLZ44N Pinout (TO-220 Package)

Hold the IRLZ44N with the metal tab facing AWAY from you,
and the text facing TOWARD you. The three legs point down:

```
        ┌────────────┐
        │  IRLZ44N   │  ← metal tab (heatsink)
        │            │
        └──┤  ├──┤  ├──┘
           │  │  │  │
           1  2  3
           │  │  │
         Gate Drain Source
```

| Pin | Name | Function |
|-----|------|----------|
| 1 (left) | **Gate** | Control input — where GP15 connects |
| 2 (center) | **Drain** | One side of the load — where Bell (−) connects |
| 3 (right) | **Source** | Ground return — connects to both GND rails |

**⚠️ CRITICAL:** The metal tab on the IRLZ44N is internally connected to the **Drain**.
Do NOT let it touch anything conductive. If you're mounting it to a heatsink,
use an insulating pad (mica or silicone).

---

## 4. Step-by-Step Wiring

### Step 1: Power the Pico 2 W

- Plug the Micro-USB cable into the Pico
- The other end goes into any USB port, phone charger, or USB adapter
- The Pico's built-in LED (GP25) should light up — confirms it's powered

**Do NOT connect the 12V PSU to the Pico. The Pico runs on USB 5V only.**

### Step 2: Insert the CR2032 into the DS3231

- Find the battery holder on the DS3231 breakout board
- Slide the CR2032 in with the **+ side facing up** (the side with text)
- The battery keeps the RTC running when the Pico loses power

### Step 3: Wire the DS3231 RTC to the Pico

The DS3231 breakout board has 4 pins (sometimes labeled on the back):

```
DS3231 Board          Pico 2 W
──────────            ────────
VCC  ──────────────── Pin 36  (3V3 OUT)
GND  ──────────────── Pin 38  (GND)
SDA  ──────────────── Pin 6   (GP4 / I2C SDA)
SCL  ──────────────── Pin 7   (GP5 / I2C SCL)
```

**Wire colors (convention, not required):**
- VCC → Red wire
- GND → Black wire
- SDA → Blue or Green wire
- SCL → Yellow or White wire

**⚠️ VCC goes to Pin 36 (3V3 OUT), NOT Pin 39 (3V3 EN) or VSYS.**
The DS3231 runs on 3.3V. Applying 5V will destroy it.

### Step 4: Wire the Manual Push Button

The tactile button has 4 pins (2 pairs, internally connected):

```
Button (top view):
    ┌─────┐
  1 │     │ 3      ← pins 1 and 3 are internally connected
    │  ○  │
  2 │     │ 4      ← pins 2 and 4 are internally connected
    └─────┘
```

Wiring:
```
Pico Pin 19 (GP14) ──── Button Pin 1 (or 3)
Pico Pin 38 (GND)  ──── Button Pin 2 (or 4)
```

**How it works:** GP14 has an internal pull-up resistor (configured in code).
When the button is not pressed, GP14 reads HIGH (3.3V).
When pressed, GP14 connects to GND → reads LOW → triggers bell ring.

**⚠️ Do NOT add an external pull-up or pull-down resistor.**
The code already enables the internal pull-up on GP14.

### Step 5: Wire the Gate Resistor

The 220Ω resistor goes **between the Pico's GP15 and the MOSFET's Gate.**

```
Pico Pin 20 (GP15) ──── [220Ω Resistor] ──── MOSFET Pin 1 (Gate)
```

**Why 220Ω:**
- Limits inrush current when GP15 switches (protects the Pico pin)
- Still fast enough to fully turn on the MOSFET in microseconds
- Without it, the gate acts as a capacitor and draws a large spike

**The resistor is NOT optional.** Connecting GP15 directly to the Gate can damage the Pico over time.

### Step 6: Wire the 12V Bell Circuit

This is the high-power side. **Double-check every connection before powering on.**

```
12V PSU (+) wire ──────────────► Bell (+) terminal

12V PSU (−) wire ──────────────► MOSFET Pin 3 (Source)

Bell (−) terminal ─────────────► MOSFET Pin 2 (Drain)

Pico Pin 38 (GND) ────────────► MOSFET Pin 3 (Source)  [same node as 12V PSU (−)]
```

**In plain words:**
1. The positive wire from the 12V adapter goes to the Bell's positive terminal
2. The negative wire from the 12V adapter goes to the MOSFET's Source (pin 3)
3. The Bell's negative terminal goes to the MOSFET's Drain (pin 2)
4. The Pico's GND also connects to the MOSFET's Source (shared ground)

**⚠️ CRITICAL: The Pico GND and 12V PSU GND must be connected.**
This is what lets the 3.3V logic signal control the 12V circuit.
If you forget this, the MOSFET won't switch.

### Step 7: Wire the Flyback Diode (1N4007)

The flyback diode goes **across the Bell terminals** — NOT across the MOSFET.

```
1N4007 Diode:
  ──►|──
  Anode  Cathode (stripe end)
```

```
Bell (+) terminal ──── Diode CATHODE (stripe/bar end)
Bell (−) terminal ──── Diode ANODE (no stripe end)
```

**The stripe on the diode MUST face toward the Bell (+) terminal.**

**Why:** When the bell turns off, the magnetic field in the bell's coil collapses
and generates a reverse voltage spike (can be 100V+). The diode gives this spike
a safe path to circulate, instead of hitting the MOSFET and destroying it.

**⚠️ If you install the diode backwards, it will conduct during normal operation
and the bell will always be weakly powered. Worse, it provides zero protection
when the bell turns off — the MOSFET will die.**

### Step 8: Status LED

The Pico 2 W has a built-in LED on GP25. No external LED is needed.
The LED turns on during bell ringing and during the boot self-test.

---

## 5. Complete Wiring Diagram (ASCII)

```
                              ┌─────────────────────────────────────────────┐
                              │            RASPBERRY PICO 2 W               │
                              │                                             │
  USB 5V ───────────────────► │ USB     (powers the Pico)                   │
                              │                                             │
                              │ Pin 36 (3V3 OUT) ──── DS3231 VCC            │
                              │ Pin 38 (GND)     ─┬── DS3231 GND           │
                              │                   ├── Button Pin 2          │
                              │                   ├── 12V PSU (−)           │
                              │                   └── MOSFET Source (pin 3) │
                              │                                             │
                              │ Pin 6  (GP4/SDA) ──── DS3231 SDA           │
                              │ Pin 7  (GP5/SCL) ──── DS3231 SCL           │
                              │                                             │
                              │ Pin 19 (GP14)    ──── Button Pin 1          │
                              │                                             │
                              │ Pin 20 (GP15)    ──── [220Ω]──► Gate (1)   │
                              │                                             │
                              │ GP25 (built-in LED) — status indicator      │
                              └─────────────────────────────────────────────┘


  ┌─────────────── 12V POWER CIRCUIT ────────────────┐
  │                                                   │
  │  12V PSU (+) ──────► Bell (+)                     │
  │                       │                           │
  │                       │◄──── 1N4007 Cathode (▬)  │
  │                       │      (stripe = this end)  │
  │                       │                           │
  │  12V PSU (−) ───► MOSFET Source (3)               │
  │  Pico GND ──────► MOSFET Source (3)               │
  │                                                   │
  │  Bell (−) ──────► MOSFET Drain (2)                │
  │                   │                               │
  │                   └──── 1N4007 Anode              │
  │                                                   │
  │  MOSFET Gate (1) ◄── [220Ω] ◄── Pico GP15        │
  └───────────────────────────────────────────────────┘
```

---

## 6. Common Mistakes and How to Avoid Them

### Mistake 1: Connecting 12V to the Pico
**Symptom:** Immediate smoke, dead Pico.
**Cause:** Running the Pico's 3V3 pin or any GPIO from the 12V rail.
**Fix:** The Pico is powered ONLY via USB. The 12V PSU powers ONLY the bell.

### Mistake 2: Forgetting the shared ground
**Symptom:** Bell never rings. GP15 toggles but MOSFET doesn't switch.
**Cause:** The Pico's GND and the 12V PSU's GND are not connected.
**Fix:** Run a wire from Pico Pin 38 to the 12V PSU's (−) terminal.

### Mistake 3: Using IRF540N instead of IRLZ44N
**Symptom:** Bell doesn't ring or rings weakly.
**Cause:** IRF540N needs 10V on the gate to fully turn on. The Pico outputs 3.3V.
The MOSFET is in "linear mode" — partially on, gets hot, bell barely works.
**Fix:** Use IRLZ44N (or IRLZ44NPBF). The "L" means logic-level — fully on at 3.3V.

### Mistake 4: Flyback diode installed backwards
**Symptom:** Bell rings weakly or always has a faint buzz. MOSFET eventually dies.
**Cause:** Diode anode and cathode are swapped.
**Fix:** The stripe (cathode) must face the Bell (+) terminal. Re-read Step 7.

### Mistake 5: No gate resistor
**Symptom:** Works initially but Pico GP15 dies after weeks/months.
**Cause:** MOSFET gate is a capacitor. Without a resistor, the Pico dumps
unlimited current into it during switching.
**Fix:** Always include the 220Ω resistor between GP15 and Gate.

### Mistake 6: SDA/SCL swapped
**Symptom:** "RTC DS3231 not found" message on boot.
**Cause:** DS3231 SDA wire connected to Pico SCL, or vice versa.
**Fix:** SDA→GP4(Pin6), SCL→GP5(Pin7). Swap them and try again.

### Mistake 7: Button wired with external pull-down resistor
**Symptom:** Bell rings constantly or never triggers.
**Cause:** Adding an external resistor conflicts with the code's internal pull-up.
**Fix:** Wire the button directly between GP14 and GND. No extra resistors.

### Mistake 8: DS3231 VCC connected to 5V (VSYS or VBUS)
**Symptom:** DS3231 gets hot and dies. Magic smoke.
**Cause:** DS3231 is a 3.3V device. 5V kills it.
**Fix:** Always connect VCC to Pin 36 (3V3 OUT).

---

## 7. Testing Procedure

### Test 1: Power-On Self-Test (POST)
1. Connect USB to the Pico (don't connect 12V yet)
2. The built-in LED should blink 3 times rapidly
3. You should hear a short 200ms beep from the bell (if 12V is connected)
4. Serial output: `BOOT Self-test OK`

### Test 2: RTC Detection
1. Connect USB and open a serial monitor (115200 baud)
2. Look for: `RTC DS3231 found on I2C (SDA=4, SCL=5)`
3. If you see `RTC DS3231 not found` — check SDA/SCL wiring

### Test 3: Bell Ring
1. Connect the 12V PSU to the bell circuit
2. Press the manual button (GP14)
3. Serial output: `BELL Manual button pressed` → `BELL ring(single_ring, 3s) queued`
4. The bell should ring for 3 seconds

### Test 4: WiFi / Dashboard
1. If `wifi.json` has no SSID → Pico creates `SBRRBell_AP` hotspot
2. Connect your phone/laptop to `SBRRBell_AP` (password: `bellsystem`)
3. Open `http://192.168.4.1` in a browser
4. Login: `admin` / `admin123`
5. Use the dashboard to ring the bell and set schedules

---

## 8. Power Budget

| Component | Voltage | Current | Notes |
|-----------|---------|---------|-------|
| Pico 2 W | 5V USB | ~100mA | With WiFi active, peaks at ~200mA |
| DS3231 | 3.3V | ~0.2mA | Negligible, powered from Pico 3V3 OUT |
| IRLZ44N Gate | 3.3V | ~0mA | Gate is capacitive, draws current only during switching |
| 12V Bell | 12V | 0.5–3A | Depends on bell model |

**The Pico's 3V3 OUT pin can supply up to 300mA.** The DS3231 draws <1mA.
No external 3.3V regulator is needed.

**The 12V PSU must be rated for the bell's current draw.**
Check the bell's label. A 1A PSU is fine for most school bells.
For sirens or heavy bells, use a 3–5A PSU.

---

## 9. Final Checklist Before Powering On

- [ ] Pico powered via USB only (no 12V on any Pico pin)
- [ ] DS3231 VCC → Pin 36 (3V3 OUT), NOT VSYS or VBUS
- [ ] DS3231 GND → Pin 38
- [ ] DS3231 SDA → Pin 6 (GP4)
- [ ] DS3231 SCL → Pin 7 (GP5)
- [ ] Button → Pin 19 (GP14) to Pin 38 (GND), no extra resistors
- [ ] 220Ω resistor between Pin 20 (GP15) and MOSFET Gate (pin 1)
- [ ] MOSFET Source (pin 3) → 12V PSU (−) AND Pico GND (Pin 38)
- [ ] MOSFET Drain (pin 2) → Bell (−) terminal
- [ ] 12V PSU (+) → Bell (+) terminal
- [ ] 1N4007 across Bell: stripe end (cathode) → Bell (+), other end → Bell (−)
- [ ] CR2032 inserted in DS3231 with + side up
- [ ] Metal tab of MOSFET is not touching anything conductive

---

*Guide for ABS-SBRR v3 — Automated Bell System for Schools*
*See [README.md](README.md) for software setup and configuration.*

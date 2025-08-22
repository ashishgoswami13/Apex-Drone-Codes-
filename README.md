# Apex G-149 Drone Control Suite (Wi‑Fi)

Apex G-149 Scratch Drone is coded using Python from the Protocol Sheet that was provided by the company.

This repository contains multiple focused Python scripts for controlling the drone over:
- Wi‑Fi (TCP JSON-in-CTP framing; mirrors 14-byte control structure)

For convenience, a packaged Python App.exe (tkinter GUI) is also created to reduce dependency setup for non-technical users (just run the executable).

![image alt](https://github.com/ashishgoswami13/Apex-Drone-Codes-/blob/432008d43d089284270a32afe5d218fbf488f941/Drone%20Control%20App.png)

## Demo Video 

<p align="center">
  <video src="https://github.com/user-attachments/assets/86ac5929-54aa-4787-9914-3ccdb99d11fc" controls width="600"> </video>
</p>

---

## Files & What They Do

Wi‑Fi Control Scripts:
- drone_wifi_control.py — Core manual controller (gyro auto-calibration, safe land-on-exit, yaw + directional)
- drone_hula_loop.py — Adds an automated “Hula Hoop” forward/ascend/descend path (menu key: h)
- drone_rectangle.py — Automated rectangle flight (key: r) + manual controls
- drone_circle_rect.py — Rectangle (r) and circular path (o) using combined pitch + yaw
- drone_ver_circle.py — User-defined forward / ascend / backward / descend sequence (key: v)
- drone_step.py — “Apex” staircase style progressive forward + altitude pattern (key: v)

Shared Concepts:
- create_wifi_command / create_packet — Build JSON control frames for Wi‑Fi
- Continuous sender thread — Keeps last command “alive” (avoid failsafe hover)
- Data receiver — Parses NOTIFY packets for altitude (low/high bytes) & battery

---

## Quick Start (Wi‑Fi)

1. Connect PC to drone’s Wi‑Fi (default 192.168.1.1).
2. Run one script, e.g.:  
   python drone_wifi_control.py  
3. Use menu keys (takeoff = 1, land = 2, movement keys vary slightly per script).
4. Type exit / q (depending on script) for safe shutdown.

---

## Automated Sequences (Summary)

| Script | Sequence |
|--------|----------|
| drone_hula_loop.py | Ascend → forward legs → partial descend → forward → land |
| drone_rectangle.py / drone_circle_rect.py | Four-sided rectangle |
| drone_circle_rect.py | Circular arc using pitch + yaw |
| drone_ver_circle.py | Custom forward → ascend → backward → descend pattern |
| drone_step.py | Staircase (forward + altitude increments) |

All sequences burst send TAKEOFF / LAND and interleave timed motion + STOP stabilization.

---

## Telemetry

- Wi‑Fi: Altitude = signed mm from bytes D8 (low) + D9 (high), converted to cm; battery = D10

---

## GUI App (App.exe)

A tkinter-based packaged executable (not shown here) wraps core controls:
- Eliminates need for manual dependency installs
- Provides buttons for takeoff, land, movement, and sequence start

---

## Disclaimer

Not affiliated with the drone manufacturer. Educational / experimental use only.

## Contact

ashishgoswami2121@gmail.com

---


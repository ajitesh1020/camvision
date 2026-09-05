# CamVision setup guide

How to build a LinuxCNC config for your 6060 iCam depaneling machine and run the
CamVision GUI beside AXIS. CamVision does **not** ship a machine config — you
create one for your hardware and drop in the pieces below. Everything here is
derived from the working legacy `6060_iCam_SPM` config, so the values match your
machine; adjust anything that differs.

- [1. Prerequisites](#1-prerequisites)
- [2. Install the CamVision app](#2-install-the-camvision-app)
- [3. Create your LinuxCNC config](#3-create-your-linuxcnc-config)
- [4. Core HAL wiring you must add](#4-core-hal-wiring-you-must-add)
- [5. Auxiliary I/O (light tower, buttons, auto-home, safety)](#5-auxiliary-io)
- [6. Launch CamVision beside AXIS](#6-launch-camvision-beside-axis)
- [7. First-run setup in the GUI](#7-first-run-setup-in-the-gui)
- [8. Keep the camera on a stable port](#8-keep-the-camera-on-a-stable-port)
- [9. Daily workflow](#9-daily-workflow)
- [Appendix A: pin map](#appendix-a-pin-map)
- [Appendix B: machine parameters](#appendix-b-machine-parameters)

---

## 1. Prerequisites

Debian 12 (bookworm) + LinuxCNC 2.9. Install the runtime libraries with the
system packages (they pair with LinuxCNC's own `linuxcnc`/`hal` Python modules):

```bash
sudo apt-get update
sudo apt-get install python3-pyqt5 python3-opencv python3-numpy
```

## 2. Install the CamVision app

Clone the repo somewhere stable (this is the Python GUI only — no machine config):

```bash
git clone https://github.com/ajitesh1020/camvision.git ~/camvision
```

Verify it runs off the machine (uses stubs + a synthetic frame, no CNC needed):

```bash
cd ~/camvision
QT_QPA_PLATFORM=offscreen python3 -m pytest -q      # expect all green
```

## 3. Create your LinuxCNC config

Make a config directory under `~/linuxcnc/configs/`, e.g. `my_6060/`, containing:

```
my_6060/
  my_6060.ini
  my_6060.hal          # your motion/spindle HAL (see §4 for the CamVision bits)
  aux_io.hal           # optional — copy from ~/camvision/docs/hal/aux_io.hal (§5)
  auto_home.clp        # optional — copy from ~/camvision/docs/hal/auto_home.clp
  launch_camvision.sh  # GUI launcher (§6)
  tool.tbl
```

Start the INI from the parameters in [Appendix B](#appendix-b-machine-parameters)
(axis travel, scales, homing, spindle). The minimum INI skeleton:

```ini
[EMC]
MACHINE = my_6060
VERSION = 1.1

[DISPLAY]
DISPLAY = axis
CYCLE_TIME = 0.100
POSITION_OFFSET = RELATIVE
POSITION_FEEDBACK = ACTUAL
DEFAULT_LINEAR_VELOCITY = 10.0
MAX_LINEAR_VELOCITY = 100.0
INTRO_TIME = 3

# Launch the CamVision GUI once AXIS is up:
[APPLICATIONS]
DELAY = 3
APP = ./launch_camvision.sh

[KINS]
JOINTS = 3
KINEMATICS = trivkins coordinates=XYZ

[EMCMOT]
EMCMOT = motmod
BASE_PERIOD = 25000
SERVO_PERIOD = 1000000

[HAL]
HALFILE = my_6060.hal
HALFILE = aux_io.hal        # optional; omit if you don't use the aux panel
HALUI = halui

[HALUI]
MDI_COMMAND = G28
MDI_COMMAND = G10 L20 P0 X0 Y0
MDI_COMMAND = G0 X0 Y0

[TRAJ]
COORDINATES = X Y Z
LINEAR_UNITS = mm
ANGULAR_UNITS = degree

[EMCIO]
EMCIO = io
TOOL_TABLE = tool.tbl
```

> Tip: you can generate the motion/spindle/homing HAL with **stepconf** for the
> 6060 (Appendix B has the numbers), then add only the CamVision-specific nets
> from §4 and the `aux_io.hal` include from §5.

## 4. Core HAL wiring you must add

Whatever produces your motion HAL, CamVision needs these **digital outputs** so
the GUI (and G-code M-codes) can drive the pneumatic camera cylinder and the
safety sensor. Add to your main `.hal` (needs `num_dio=32` on the motmod load):

```hal
# loadrt motmod ... num_dio=32     <-- ensure this on your EMCMOT load

# Pneumatic camera cylinder: M64 P0 = deploy (camera DOWN to inspect),
#                            M65 P0 = retract (camera UP for cutting).
net camera_up_down       <= motion.digital-out-00 => parport.1.pin-16-out

# Safety / fixture sensor enable: M64 P1 / M65 P1.
net enable_safety_sensor <= motion.digital-out-01 => parport.1.pin-17-out
```

Spindle: CamVision emits `M3 S<rpm>`; wire the spindle as a PWM output scaled to
your VFD max (the legacy machine used a `pwmgen` scaled to **24000** RPM on
`parport.0.pin-01-out`, with spindle-on on `parport.0.pin-17-out` /
`parport.1.pin-07-out`). See Appendix B.

Zeroing (`Set X/Y Zero` button) issues `G10 L20 P0 X0 Y0`; skew correction issues
`G10 L2 P0 R<deg>`. Both need `HALUI = halui` in the INI (above) and a homed
machine. No extra HAL is required for those.

## 5. Auxiliary I/O

If your machine has the indicator light tower, external buttons and
ClassicLadder auto-home, copy the ready-made snippet and its ladder program into
your config directory and include it from the INI (§3 shows the `HALFILE =
aux_io.hal` line):

```bash
cp ~/camvision/docs/hal/aux_io.hal    ~/linuxcnc/configs/my_6060/
cp ~/camvision/docs/hal/auto_home.clp ~/linuxcnc/configs/my_6060/
```

Read the header of `aux_io.hal` first: it `loadrt`s the logic components
(`and2`, `or2`, `siggen`, `classicladder_rt`, …). **If your base HAL already
loads any of them, delete the duplicate `loadrt` in the snippet and raise the
existing `count=` instead** — a double load aborts HAL startup. The snippet
provides: home-all/pause/resume/run/step/cycle-stop buttons, a red/green/amber
light tower, and a safety-curtain interlock that pauses a running program.

## 6. Launch CamVision beside AXIS

Create `launch_camvision.sh` in your config dir and make it executable:

```bash
#!/bin/bash
set -e
CONFIG_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HOME/camvision:$PYTHONPATH"      # path to the cloned repo
exec python3 -m camvision.app "$CONFIG_DIR/config.json"
```

```bash
chmod +x ~/linuxcnc/configs/my_6060/launch_camvision.sh
```

The `[APPLICATIONS] APP = ./launch_camvision.sh` line (§3) starts it a few
seconds after AXIS opens. CamVision stores its own `config.json` in the config
dir (override with the `CAMVISION_CONFIG` env var). To launch it by hand while
LinuxCNC is running:

```bash
PYTHONPATH=~/camvision python3 -m camvision.app ~/linuxcnc/configs/my_6060/config.json
```

## 7. First-run setup in the GUI

1. **Home** all axes in AXIS.
2. In CamVision **Setup**:
   - **Camera**: click **Detect / pin camera** to store a stable `/dev/v4l/by-id`
     handle (see §8). Set flip/rotation so the view matches reality.
   - **mm / pixel**: measure it — click two ends of a known length (e.g. a 10 mm
     gauge) at working height; enter the resulting factor.
   - **Camera-to-spindle offset (X/Y)**: the fixed distance from the camera axis
     to the spindle tip. The legacy 6060 measured **X = 109.448, Y = 9.91 mm**;
     re-measure for your build (jog a feature under the crosshair, note XY; jog it
     under the spindle, note XY; the difference is the offset).
   - **Inspect Z**: machine Z the camera drops to when inspecting (legacy 78.0),
     plus your fixture and baseplate thickness fields.
   - **Fiducial** (optional): tick *enable*, set the HoughCircles params and drag
     the ROI; teach the two fiducial machine positions.
3. Everything you set is saved to `config.json`. Migrating from the old tool? Seed
   it from your existing file so nothing is re-measured:
   ```python
   from camvision.config import import_legacy_config
   import_legacy_config("old/config.json", "~/linuxcnc/configs/my_6060/config.json")
   ```

## 8. Keep the camera on a stable port

A USB camera can move to a different `/dev/videoN` after ~10–15 min (kernel
re-enumeration, usually from USB autosuspend). CamVision handles this by opening a
**stable `/dev/v4l/by-id` handle** (Setup → *Detect / pin camera*) and
**auto-reconnecting** if frames stop. Also stop the cause — disable autosuspend
for the camera (find its `idVendor:idProduct` with `lsusb`):

```bash
# /etc/udev/rules.d/50-camvision-usb.rules  (replace 1a2b:3c4d)
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="1a2b", ATTR{idProduct}=="3c4d", TEST=="power/control", ATTR{power/control}="on"
```

```bash
sudo udevadm control --reload && sudo udevadm trigger
ls -l /dev/v4l/by-id/       # shows the stable handles
```

## 9. Daily workflow

1. Jog so the crosshair sits on the PCB corner/edge → **Set X/Y Zero**.
2. **Teach**: capture start → add line / 3-point arc / circle; set cut Z per
   segment; enter program name + operator.
3. **Simulate**: Build → Play — the tool traces the offset-compensated path at
   safe Z (what you see is what gets cut).
4. **Export G-code** (`.ngc`) or **Save** the `.cvprog` to edit later.
5. Load the `.ngc` in AXIS and run (run the fiducial cycle first if enabled).

---

## Appendix A: pin map

Parallel ports loaded as `hal_parport cfg="e100 out d100 out"` →
`parport.0` (e100) and `parport.1` (d100).

| Signal | Pin | Dir |
|---|---|---|
| Spindle PWM | parport.0 p01 | out |
| X step / dir | parport.0 p02 / p03 | out |
| Y step / dir | parport.0 p04 / p05 | out |
| Z step / dir | parport.0 p06 / p07 | out |
| Spindle on | parport.0 p17, parport.1 p07 | out |
| Home X / Y / Z | parport.0 p10 / p11 / p12 | in |
| Safety curtain | parport.0 p13 | in |
| E-stop (n/c) | parport.0 p15 | in |
| **Camera cylinder** (M64/M65 P0) | parport.1 p16 | out |
| **Safety sensor** (M64/M65 P1) | parport.1 p17 | out |
| Light tower RED / GREEN / AMBER | parport.1 p14 / p08 / p09 | out |
| Button: home-all / pause / run-step / stop | parport.1 p11 / p12 / p10 / p13 | in |

## Appendix B: machine parameters

3-axis XYZ, trivkins, millimetres. Travel and homing from the legacy config:

| Axis | Travel (mm) | Scale (steps/mm) | Home | Home seq | Search / latch vel |
|---|---|---|---|---|---|
| X (J0) | 0 … 515 | 400.0 | 0.0 | 1 | +30 / +1.25 |
| Y (J1) | 0 … 695 | 399.8 | 695.0 | 1 | −30 / −1.25 |
| Z (J2) | 0 … 120 | 400.0 | 120.0 | 0 | −20 / −1.25 |

Common per-joint: `MAX_VELOCITY ≈ 95`, `MAX_ACCELERATION = 750`,
`STEPGEN_MAXACCEL = 937.5`, `FERROR = 1`, `MIN_FERROR = 0.25`, stepgen
`steplen=1 stepspace=0 dirhold=26000 dirsetup=26000`.

Spindle: `pwmgen output_type=1`, `pwm-freq 1000`, `scale 24000` (max RPM),
`offset 0`, `dither-pwm true`.

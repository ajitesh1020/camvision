# CamVision

A camera-guided GUI for a **LinuxCNC PCB depaneling machine** (6060, 3-axis).
CamVision runs **beside the AXIS GUI** and lets an operator align to a panel with
an on-screen crosshair, **teach cut paths** by jogging, **simulate** them in real
time, and **run** — with the fixed **camera-to-spindle offset** compensated so the
router cuts exactly where the camera saw the line.

This is a clean, modular rewrite of an older single-file `cam_align` tool. Pure
logic (geometry, program model, G-code, fiducial math, config) is separated from
Qt and from LinuxCNC, so it is unit-tested on any machine and only the thin
`ui` / `camera` / `machine` layers touch hardware.

## What it does

- **Live camera view** (OpenCV) with a crosshair, adjustable centre circle, and a
  fiducial ROI you drag on the image.
- **Set X/Y zero**: align the crosshair to the physical PCB edge and set the G54
  work zero (`G10 L20 P0 X0 Y0`).
- **Mouse-click jog** on the camera image:
  - **short click** → a slow feed move so the clicked point comes under the
    crosshair (precise "go to what I clicked");
  - **long press / hold** → continuous rapid jog toward the click until release.
  On-screen jog buttons follow the same tap=slow-step / hold=rapid rule.
- **Teach** linear cuts (capture start → add line), **arcs** (3-point capture →
  true G2/G3), and **full circles** (centre + radius). Programs carry a **name**
  and **operator**, save as editable `.cvprog`, and reload for editing.
- **Real-time simulation** overlaid on the view, tracing the **actual cutting
  path** — camera offset applied, Z at the safe height — from the same geometry
  the G-code uses, so what you see is what gets cut.
- **Fiducial correction** (optional, toggle in Setup): runs entirely inside the
  GUI (it owns the camera — no shared memory), detecting two fiducials and
  applying a rotation with `G10 L2 P0 R`.
- **Pneumatic camera cylinder** up/down (`M64/M65 P0`) and safety-sensor control.

## Layout

```
camvision/
  app.py                     entry point (launched by LinuxCNC [APPLICATIONS])
  config.py                  ConfigManager: typed, atomic JSON (legacy-compatible)
  machine/                   LinuxCNC command/status wrapper + off-machine stubs
  camera/service.py          threaded OpenCV capture, single in-process frame owner
  vision/                    geometry, calibration, fiducial detection + math
  program/                   program model, G-code, .cvprog store, simulator
  ui/                        PyQt5 panels (camera view, jog, teach, simulate, setup)
  fiducial_cycle.py          in-GUI fiducial correction cycle
configs/camvision_6060/      LinuxCNC .ini/.hal/macros + GUI launcher
tests/                       pytest suite (runs headless, no CNC/camera needed)
```

## Install (Debian 12 / LinuxCNC 2.9)

Use the system packages on the machine:

```bash
sudo apt-get install python3-pyqt5 python3-opencv python3-numpy
```

LinuxCNC provides the `linuxcnc` and `hal` Python modules. Clone this repo (e.g.
to `~/camvision`) and symlink the config into your LinuxCNC configs:

```bash
git clone https://github.com/ajitesh1020/camvision.git ~/camvision
ln -s ~/camvision/configs/camvision_6060 ~/linuxcnc/configs/camvision_6060
chmod +x ~/camvision/configs/camvision_6060/launch_camvision.sh
chmod +x ~/camvision/configs/camvision_6060/macros/M10*
```

Launch LinuxCNC and pick **camvision_6060**. AXIS opens and, a few seconds later,
the CamVision window opens beside it (via the INI `[APPLICATIONS]` launcher).

## First-time setup

1. **Home** the machine in AXIS.
2. In CamVision **Setup**: set the camera `mm/pixel`, the **camera-to-spindle
   offset** (X/Y, mm), the **inspect Z**, camera orientation, and (optionally)
   fiducial params + enable. Everything saves to `config.json` in the config dir.
3. Migrating from the old tool? Seed the config from your existing `config.json`:
   ```python
   from camvision.config import import_legacy_config
   import_legacy_config("old/config.json", "configs/camvision_6060/config.json")
   ```
   Calibration, ROI and the measured offset carry over.

## Workflow

1. Jog so the crosshair sits on the PCB corner/edge → **Set X/Y Zero**.
2. **Teach** tab: capture start, add line / arc / circle; set cut Z per segment;
   enter program name + operator.
3. **Simulate** tab: **Build** then **Play** — the tool tracks the offset-
   compensated path at safe Z.
4. **Export G-code** (`.ngc`) or **Save** the `.cvprog` to edit later.
5. Load the `.ngc` in AXIS and run (optionally run the fiducial cycle first).

## Troubleshooting: the camera "port" changes on its own

A USB (UVC) camera opened by a bare index — `cv2.VideoCapture(3)` → `/dev/video3`
— can move to a **different `/dev/videoN`** while running (often ~10–15 min in).
The app didn't change the port; the **kernel re-enumerated the device**, usually
because USB **autosuspend** powered it down and it came back on a new node. This
was the legacy tool's biggest camera bug.

CamVision fixes it two ways:

1. **Pin a stable handle.** In **Setup → Stable handle**, click **Detect / pin
   camera**: it finds the `/dev/v4l/by-id/usb-...-video-index0` symlink for your
   selected index and stores that instead of the number. udev keeps that symlink
   pointed at the *same physical camera* no matter which `/dev/videoN` it becomes,
   so the feed never follows the wrong node. (No serial? Paste a
   `/dev/v4l/by-path/...` handle, which is stable per USB port.)
2. **Auto-reconnect.** If frames stop, the camera service releases, re-resolves
   the handle (picking up the new node) and reopens automatically — you see
   `RECONNECTING…` briefly instead of a frozen image.

**Also fix the root cause** — stop the device suspending. Disable USB autosuspend
for the camera (find its `ID_VENDOR:ID_PRODUCT` with `lsusb`):

```bash
# /etc/udev/rules.d/50-camvision-usb.rules  (replace 1a2b:3c4d with your camera)
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="1a2b", ATTR{idProduct}=="3c4d", TEST=="power/control", ATTR{power/control}="on"
```

Then `sudo udevadm control --reload && sudo udevadm trigger`. (A blanket kernel
option `usbcore.autosuspend=-1` also works but affects all USB devices.) List a
camera's stable handles any time with `ls -l /dev/v4l/by-id/`.

## Development / tests

Runs with no CNC and no camera — the machine layer falls back to stubs and the
camera emits a synthetic frame:

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen pytest -q
```

CI (`.github/workflows/ci.yml`) runs the same suite headless on each push.

## Notes on the rewrite

- The brittle **shared-memory frame hand-off** between the GUI and a fiducial
  subprocess is gone; the GUI owns the camera and runs the fiducial cycle
  in-process.
- Config I/O is centralised in one `ConfigManager` (was rewritten in ~15 places).
- Jogging uses the native `linuxcnc.command.jog` (continuous + incremental)
  instead of a qtvcp dependency.
- The machine `.ini`/`.hal` preserve every working pin/signal from the legacy
  `6060_iCam_SPM` config. Core motion/spindle/camera wiring is in
  `camvision_6060.hal`; the auxiliary machine I/O (indicator light tower,
  external pause/run/stop/home buttons, ClassicLadder auto-home via
  `auto_home.clp`, and the safety-curtain interlock) is in `custom.hal`. The
  PyVCP-panel display bits from the old config are dropped — CamVision's own GUI
  replaces them.

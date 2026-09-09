# Verifying a program before you cut

CamVision has **two real-machine dry-runs** plus an on-screen preview. Every
dry-run keeps Z at the **safe height**, so nothing ever touches the PCB — they
only move XY so you can watch the path.

## First: set the Safe Z

Jog Z to a height that clears the fixture, then press **Set Safe Z (here)** (under
*Set Camera Zero + Spindle G55* on the jog side). Programs retract to this height and both dry-runs
stay at it. This is the equivalent of the legacy "Z height" button.

## Camera G54 and spindle G55 zero

With the camera crosshair on the PCB reference, press **Set Camera Zero + Spindle
G55**. CamVision sets camera X/Y zero in G54 and makes G55 X/Y zero coincide with
the spindle at the same reference using the saved camera-to-spindle offset. G55
inherits G54's Z reference, so cut depth, Retract Z, and Safe Z have the same
meaning in both coordinate systems.

When **Export using spindle G55 zero** is enabled in Setup → G-code, cutting
programs select G55 and use the taught positive X/Y values. The program returns
to G54 after a normal finish. Leave this option off to use the original G54
export, where the offset is subtracted into each G-code XY value.

## Set a visible simulation speed

In **Setup → G-code**, set **Simulation feed** in mm/min. Camera-follow and
Spindle-path use this one speed for each taught cutting segment (`G1`/`G2`/`G3`).
Non-cutting travel to the next segment start remains a rapid `G0` move, matching
the real cutting program. This setting affects only simulation; it does not
change the feed rates in exported cutting G-code.

## The two simulation modes (Simulate tab)

### 1. Run: Camera-follow — checks the *teaching*
Camera is deployed (down) and the machine traces the **taught** path (no offset)
at the safe Z. Watch the **crosshair follow the cut line** on the actual PCB. If
the crosshair rides along the lines you intended, the teaching is correct.

### 2. Run: Spindle-path — checks the *offset*
Camera is retracted (up) and the machine traces the **offset-compensated** path at
the safe Z, so the **spindle moves over exactly where it will cut**. Watch the
spindle tip trace the intended cut lines. If it's shifted, the camera→spindle
offset is wrong — measure it (below).

**Preview** just draws the compensated path on the camera view (no motion) for a
quick sanity check. Press **Stop** to abort a running dry-run.

## Fixing "the tool doesn't follow the exact path" (camera→spindle offset)

The spindle sits a fixed distance from the camera. If that offset is wrong (or has
the wrong sign), the whole cut lands to one side — you'll also see the **program's
zero marker on the wrong side** in AXIS. Measure it properly:

1. Setup tab → **Camera-to-spindle offset** section.
2. Jog so the **crosshair** sits exactly on a distinct feature (a fiducial, a
   drill hole, a corner) → click **1) Mark with camera**.
3. Jog so the **spindle tip** sits on the **same** feature → click
   **2) Mark with spindle**. The offset (with the correct sign) is computed and
   saved automatically.
4. Re-run **Spindle-path** simulation to confirm the spindle now traces the lines.

The offset is stored as *camera-mark − spindle-mark* and subtracted from taught
points, so after measuring, a taught point cuts exactly at the feature the camera
saw. Keep **apply_spindle_offsets** (Setup) on for real cuts.

## Quick self-check (no hardware)

```bash
cd ~/camvision
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_gcode.py tests/test_simulator.py
```

This exercises the dry-run move generation (safe-Z, no plunge, controlled feed,
offset per mode) and the path flattening the preview uses.

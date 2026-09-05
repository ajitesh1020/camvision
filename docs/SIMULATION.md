# Verifying a program with Simulation

Simulation lets you **check a taught program before you cut** — it draws the real
cutting path on the camera view and animates the tool along it, using the *same*
geometry the exported G-code uses. What you see is what the spindle will do.

## What the simulation shows

- The **cyan path** is the actual cut line: every taught point with the
  **camera-to-spindle offset applied** (so it's where the *spindle* goes, not
  where the camera saw the line), for lines, arcs and circles.
- The dashed lead-ins are the rapid moves between segments.
- The **yellow marker** is the tool position as it animates along the path.
- Z is not drawn: between segments the tool rides at the **safe height** and
  plunges to each segment's cut depth — the panel reports the safe Z and depth.

The path is scaled to fit the 640×480 view (it's a schematic of the toolpath, not
an overlay registered to the live camera image).

## Step by step

1. **Teach a program** on the **Teach** tab: Capture Start → Add Line, and/or
   3-Point Arc, and/or Add Circle. Set the Cut Z for each segment.
2. Switch to the **Simulate** tab and press **Build**. The cyan path appears and
   the info line reports the segment count, whether the offset is applied, and
   the safe Z.
3. Press **Play** to animate the tool, **Step** to advance one point at a time,
   or **Reset** to clear and rewind.
4. **Check it:** does the path match the cut lines you intended? Are the arcs
   curving the right way (G2 vs G3)? Is the whole shape in the right place once
   the offset is applied?
5. When it looks right, go back to **Teach → Export G-code**, load the `.ngc` in
   AXIS, and run it (run the fiducial cycle first if enabled).

## Tips

- Toggle **apply_spindle_offsets** (Setup) off/on and Build again to see the
  offset's effect: with it on, the path shifts by the camera-to-spindle offset —
  that shifted path is what actually gets cut.
- If Build says "Nothing to simulate", the program has no segments yet — teach
  at least one on the Teach tab.
- The simulation never moves the machine; it is safe to run any time, even with
  the spindle off or the machine not homed.

## Quick self-check (no hardware)

You can prove the simulation pipeline works without a CNC or camera:

```bash
cd ~/camvision
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_simulator.py tests/test_gui_smoke.py
```

Both should pass — that exercises the flattening math and the Build→Play path
through the real GUI (against the stub machine).

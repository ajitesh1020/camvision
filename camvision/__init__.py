"""CamVision — LinuxCNC vision GUI for PCB depaneling.

A cleanly layered rewrite of the legacy ``cam_align`` application. Pure logic
(geometry, program model, G-code, fiducial math, config) has no Qt or LinuxCNC
dependency so it runs and is unit-tested on any machine; the ``ui``, ``camera``
and ``machine`` layers add the hardware- and Qt-facing pieces.
"""

__version__ = "1.0.0"

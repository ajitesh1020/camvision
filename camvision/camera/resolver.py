"""Resolve a camera to a **stable** device handle, immune to index shuffling.

The legacy tool opened a bare integer index (``cv2.VideoCapture(3)`` →
``/dev/video3``). On Linux the ``/dev/videoN`` number is *not* stable: a UVC
camera that glitches or gets USB-autosuspended after a few minutes re-enumerates
under a different number, and the app is left pointing at the wrong node — exactly
the "port changed after 10–15 min without changing it" failure.

The fix is to identify the camera by a **udev by-id / by-path symlink**
(``/dev/v4l/by-id/usb-<vendor>_<model>_<serial>-video-index0``). udev keeps that
symlink pointed at the same physical camera no matter which ``/dev/videoN`` it
currently is, so opening the symlink (or its live realpath) always finds the
right device. This module turns a user-supplied spec into such a handle and can
discover the stable handle for a working index so the config can be *pinned*.

Directory constants are overridable so the logic is unit-testable without real
hardware.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import List, Optional, Union

BY_ID_DIR = "/dev/v4l/by-id"
BY_PATH_DIR = "/dev/v4l/by-path"


@dataclass
class CameraDevice:
    index: Optional[int]      # /dev/videoN number, if known
    dev_path: str             # concrete /dev/videoN this currently maps to
    by_id: Optional[str]      # stable /dev/v4l/by-id/... symlink, if any
    by_path: Optional[str]    # stable /dev/v4l/by-path/... symlink, if any

    @property
    def stable_handle(self) -> str:
        """Best stable handle to store in config (by-id > by-path > /dev/videoN)."""
        return self.by_id or self.by_path or self.dev_path

    @property
    def label(self) -> str:
        name = os.path.basename(self.by_id) if self.by_id else self.dev_path
        return f"{name} -> {self.dev_path}"


def _index_of(dev_path: str) -> Optional[int]:
    base = os.path.basename(dev_path)
    if base.startswith("video") and base[5:].isdigit():
        return int(base[5:])
    return None


def _symlinks(directory: str) -> List[str]:
    try:
        return sorted(glob.glob(os.path.join(directory, "*")))
    except OSError:
        return []


def list_cameras(by_id_dir: str = BY_ID_DIR, by_path_dir: str = BY_PATH_DIR) -> List[CameraDevice]:
    """Enumerate cameras, pairing each capture node with its stable symlinks.

    Only ``*-video-index0`` / the lowest node of each device is returned so a
    multi-node UVC camera (video + metadata) shows up once.
    """
    # Map concrete /dev/videoN -> stable symlinks pointing at it.
    by_id_for: dict[str, str] = {}
    by_path_for: dict[str, str] = {}
    for link in _symlinks(by_id_dir):
        if link.endswith("index0") or "video" in os.path.basename(link):
            try:
                by_id_for.setdefault(os.path.realpath(link), link)
            except OSError:
                continue
    for link in _symlinks(by_path_dir):
        if link.endswith("index0") or "video" in os.path.basename(link):
            try:
                by_path_for.setdefault(os.path.realpath(link), link)
            except OSError:
                continue

    devices: List[CameraDevice] = []
    seen = set()
    # Prefer nodes that have a stable symlink; then fall back to raw /dev/video*.
    candidates = sorted(set(by_id_for) | set(by_path_for) | set(glob.glob("/dev/video*")))
    for dev in candidates:
        real = os.path.realpath(dev)
        if real in seen:
            continue
        seen.add(real)
        devices.append(
            CameraDevice(
                index=_index_of(real),
                dev_path=real,
                by_id=by_id_for.get(real),
                by_path=by_path_for.get(real),
            )
        )
    return devices


def stable_handle_for_index(index: int, by_id_dir: str = BY_ID_DIR,
                            by_path_dir: str = BY_PATH_DIR) -> Optional[str]:
    """Return the by-id/by-path symlink that currently points at /dev/video<index>.

    Use this to convert a working numeric device into a pinnable stable handle.
    """
    target = os.path.realpath(f"/dev/video{index}")
    for directory in (by_id_dir, by_path_dir):
        for link in _symlinks(directory):
            try:
                if os.path.realpath(link) == target:
                    return link
            except OSError:
                continue
    return None


def resolve(spec: Union[int, str, None],
            by_id_dir: str = BY_ID_DIR,
            by_path_dir: str = BY_PATH_DIR) -> Union[int, str]:
    """Turn a config spec into something ``cv2.VideoCapture`` can open *now*.

    Accepted specs:

    * ``int`` / numeric string  → ``/dev/videoN`` (legacy behaviour, unstable);
    * an existing path (``/dev/v4l/by-id/...``, ``/dev/video3``) → its live
      realpath, so a by-id symlink always resolves to the current node;
    * a name fragment (e.g. ``"HD Webcam"`` or a serial) → the matching by-id
      symlink's current realpath.

    Returns a concrete ``/dev/videoN`` path when one can be found, else the int
    index (so a plain ``0`` still works on a box with no udev symlinks).
    """
    if spec is None or spec == "":
        return 0

    # Numeric spec → keep as index (least stable; recommend pinning instead).
    if isinstance(spec, int):
        return spec
    if isinstance(spec, str) and spec.isdigit():
        return int(spec)

    # A concrete or symlink path: follow it live so a by-id symlink always maps
    # to the device's current /dev/videoN.
    if isinstance(spec, str) and (spec.startswith("/dev/") or os.path.exists(spec)
                                  or os.path.islink(spec)):
        if os.path.exists(spec) or os.path.islink(spec):
            return os.path.realpath(spec)
        # Path recorded but device currently absent — return as-is so the caller
        # keeps retrying rather than silently opening index 0.
        return spec

    # Otherwise treat it as a name/serial fragment to match against by-id names.
    frag = str(spec).lower()
    for link in _symlinks(by_id_dir) + _symlinks(by_path_dir):
        if frag in os.path.basename(link).lower():
            try:
                return os.path.realpath(link)
            except OSError:
                return link
    # No match — fall back to index 0 so the app still starts.
    return 0

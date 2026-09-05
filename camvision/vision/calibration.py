"""Pixel-to-mm calibration: click a known distance at several heights, fit a line.

The operator clicks the two ends of a feature of known length (e.g. a 10 mm
gauge) at a few Z heights; each click pair yields a mm/pixel factor. A linear fit
of factor-vs-height captures how the factor changes as the camera nears the work,
and the mean factor is stored for everyday conversion. Ported from the legacy
``analyze_calibration_data`` but with the numpy ``polyfit`` dependency instead of
scipy, and split out as pure functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np


def factor_from_click_pair(p1: Tuple[float, float], p2: Tuple[float, float], known_mm: float) -> float:
    """mm-per-pixel from two clicked pixels spanning ``known_mm`` millimetres."""
    dist_px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if dist_px == 0:
        raise ValueError("The two calibration clicks are at the same pixel")
    return known_mm / dist_px


@dataclass
class CalibrationResult:
    mean_mm_per_pixel: float
    slope: float          # change in mm/pixel per mm of height (fit gradient)
    intercept: float
    samples: List[Tuple[float, float]] = field(default_factory=list)  # (height, factor)


def fit_calibration(samples: List[Tuple[float, float]]) -> CalibrationResult:
    """Fit mm/pixel vs height. ``samples`` is a list of ``(height_mm, factor)``.

    With a single sample the slope is 0 and the factor is used as-is.
    """
    if not samples:
        raise ValueError("No calibration samples provided")
    heights = np.array([h for h, _ in samples], dtype=float)
    factors = np.array([f for _, f in samples], dtype=float)
    mean = float(np.mean(factors))
    if len(samples) >= 2 and np.ptp(heights) > 0:
        slope, intercept = np.polyfit(heights, factors, 1)
    else:
        slope, intercept = 0.0, mean
    return CalibrationResult(
        mean_mm_per_pixel=mean,
        slope=float(slope),
        intercept=float(intercept),
        samples=list(samples),
    )

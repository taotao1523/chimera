"""
Physiological tremor simulation.

Human hands exhibit a natural 8–12 Hz tremor (physiological tremor),
whose amplitude increases with muscle activation (i.e., with velocity).
This module adds Gaussian noise scaled by instantaneous velocity to
trajectory points.

Reference: Elble & Randall (1978), "Mechanistic components of normal tremor"
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from chimera.humanize.trajectory import Point


@dataclass
class TremorConfig:
    # Base amplitude when stationary (pixels)
    base_amplitude_px: float = 0.25

    # Amplitude scales linearly with velocity:
    #   amplitude = base + velocity_px_per_s * velocity_scale
    velocity_scale: float = 0.012

    # Frequency band (Hz) — physiological tremor is 8–12 Hz
    # We don't generate a sine wave but use this to bound noise update rate
    frequency_hz: float = 10.0

    # Clamp maximum tremor amplitude (pixels) to avoid ridiculous jitter
    max_amplitude_px: float = 4.0


def apply_tremor(
    points: list[Point],
    duration_ms: float,
    config: TremorConfig | None = None,
) -> list[Point]:
    """Add physiological tremor noise to a trajectory.

    The noise at each point is Gaussian with std proportional to the
    local velocity (faster movement → more tremor).

    Args:
        points: Trajectory points (will be modified in place)
        duration_ms: Total movement duration in ms
        config: Tremor parameters

    Returns:
        The modified list (same object, points mutated)
    """
    if config is None:
        config = TremorConfig()

    if len(points) < 2:
        return points

    sample_interval_ms = duration_ms / len(points)

    for i in range(len(points)):
        # Estimate instantaneous velocity from adjacent points
        if i == 0:
            if len(points) > 1:
                vel = points[i].distance(points[i + 1]) / (sample_interval_ms / 1000)
            else:
                vel = 0.0
        elif i == len(points) - 1:
            vel = points[i].distance(points[i - 1]) / (sample_interval_ms / 1000)
        else:
            vel = (
                points[i - 1].distance(points[i + 1]) / 2
            ) / (sample_interval_ms / 1000)

        amplitude = config.base_amplitude_px + vel * config.velocity_scale
        amplitude = min(amplitude, config.max_amplitude_px)

        points[i] = Point(
            points[i].x + random.gauss(0, amplitude),
            points[i].y + random.gauss(0, amplitude),
        )

    return points


def apply_micro_jitter(
    point: Point, amplitude: float = 0.15
) -> Point:
    """Apply a single micro-jitter to a stationary point.

    Used for hover-over-target situations where the cursor should
    not be perfectly still.
    """
    return Point(
        point.x + random.gauss(0, amplitude),
        point.y + random.gauss(0, amplitude),
    )

"""
Bezier curve trajectory generation with minimum-jerk velocity profiling.

A human arm does not move in straight lines at constant speed. This module models:

1. Spatial path: cubic Bezier curves with randomized asymmetric control points
   — emulating the natural arc of a hand/wrist movement

2. Temporal profile: minimum-jerk (5th-order polynomial) velocity
   — produces the bell-shaped speed curve characteristic of human reaching

3. Fitts' Law: total movement duration scales with distance and target size
   — T = a + b * log₂(D/W + 1), where D=distance, W=target width

References:
- Flash & Hogan (1985), "The coordination of arm movements"
- Fitts (1954), "The information capacity of the human motor system"
- ISO 9241-9, Ergonomic requirements for pointing devices
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass
class Point:
    x: float
    y: float

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __mul__(self, scalar: float) -> Point:
        return Point(self.x * scalar, self.y * scalar)

    def distance(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def lerp(self, other: Point, t: float) -> Point:
        return Point(
            self.x + (other.x - self.x) * t,
            self.y + (other.y - self.y) * t,
        )

    def as_tuple(self) -> tuple[int, int]:
        return (int(self.x), int(self.y))


@dataclass
class TrajectoryConfig:
    """Controls the human-likeness of generated trajectories.

    All values are empirical averages derived from HCI literature (ISO 9241-9);
    tune them to match a specific "persona" (e.g., elderly user → higher a & b).
    """

    # Fitts' Law coefficients (milliseconds)
    fitts_a: float = 50.0      # reaction / dwell time
    fitts_b: float = 120.0     # speed constant (bits/ms)

    # Bezier control point deviation
    # Higher = more curvature; 0 = straight line
    curvature_max: float = 0.45
    curvature_min: float = 0.10

    # Minimum-jerk granularity (more steps = smoother)
    steps_per_px: float = 0.02  # one sample every ~50 px

    # Overshoot behavior
    overshoot_chance: float = 0.65  # probability on fast (>800px) moves
    overshoot_fraction: float = 0.05  # how far past the target
    overshoot_correction_ratio: float = 0.35  # how fast to correct

    # Minimum move duration (ms) — prevents instant micro-moves
    min_duration_ms: float = 80.0

    # Tremor injection
    tremor_enabled: bool = True
    tremor_base_amplitude: float = 0.3  # pixels at rest
    tremor_velocity_scale: float = 0.015  # px tremor per px/s velocity

    # Micro-drift when stationary (pixels/second)
    micro_drift_speed: float = 2.0


# Sensitivity curve — maps velocity fraction [0,1] to position fraction [0,1]
# This is the integral of a bell-shaped speed profile (5th-order min-jerk).
def _minimum_jerk_profile(t: float) -> float:
    """Maps normalised time t∈[0,1] to normalised arc-length s∈[0,1].

    s(t) = 10·t³ − 15·t⁴ + 6·t⁵
    This is the integral of v(t) = 30·t² − 60·t³ + 30·t⁴

    At t=0 → s=0 (stationary)
    At t=0.5 → s=0.5 (peak velocity at midpoint)
    At t=1 → s=1 (stationary)
    """
    return 10 * t**3 - 15 * t**4 + 6 * t**5


def _fitts_duration(distance_px: float, target_width_px: float, config: TrajectoryConfig) -> float:
    """Returns movement duration in milliseconds (Fitts, 1954 / ISO 9241-9)."""
    if target_width_px <= 0:
        target_width_px = 1.0
    index_of_difficulty = math.log2(distance_px / target_width_px + 1)
    duration = config.fitts_a + config.fitts_b * index_of_difficulty
    return max(duration, config.min_duration_ms)


def _generate_control_points(
    start: Point, end: Point, config: TrajectoryConfig
) -> tuple[Point, Point]:
    """Generate two Bezier control points for a natural arc.

    P1 pulls toward the start, P2 toward the end, each deviating from the
    straight line by a random fraction of the total distance.
    """
    dist = start.distance(end)
    mid = Point((start.x + end.x) / 2, (start.y + end.y) / 2)
    dx, dy = end.x - start.x, end.y - start.y

    # Perpendicular unit vector (rotate 90°)
    perp_x, perp_y = -dy, dx
    norm = math.hypot(perp_x, perp_y) or 1.0
    perp_x /= norm
    perp_y /= norm

    # Randomize the perpendicular offset direction and magnitude
    sign = random.choice([-1, 1])
    mag1 = random.uniform(config.curvature_min, config.curvature_max) * dist
    mag2 = random.uniform(config.curvature_min, config.curvature_max) * dist

    # Asymmetric: P1 tends toward start region, P2 toward end region
    p1_weight = random.uniform(0.25, 0.45)
    p2_weight = random.uniform(0.55, 0.75)

    p1 = Point(
        start.x + (end.x - start.x) * p1_weight + sign * perp_x * mag1,
        start.y + (end.y - start.y) * p1_weight + sign * perp_y * mag1,
    )
    p2 = Point(
        start.x + (end.x - start.x) * p2_weight + sign * perp_x * mag2,
        start.y + (end.y - start.y) * p2_weight + sign * perp_y * mag2,
    )
    return p1, p2


def _evaluate_cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Cubic Bezier: B(t) = (1-t)³P0 + 3(1-t)²tP1 + 3(1-t)t²P2 + t³P3"""
    mt = 1 - t
    mt2 = mt * mt
    mt3 = mt2 * mt
    t2 = t * t
    t3 = t2 * t
    return Point(
        mt3 * p0.x + 3 * mt2 * t * p1.x + 3 * mt * t2 * p2.x + t3 * p3.x,
        mt3 * p0.y + 3 * mt2 * t * p1.y + 3 * mt * t2 * p2.y + t3 * p3.y,
    )


def generate_trajectory(
    start: Point | tuple[float, float],
    end: Point | tuple[float, float],
    target_width: float = 20.0,
    config: TrajectoryConfig | None = None,
) -> tuple[list[Point], float]:
    """Generate a human-like mouse trajectory from start to end.

    Returns:
        (points, total_duration_ms):
        - points: ordered list of screen-coordinate Points
        - total_duration_ms: total time this movement should take

    Usage:
        pts, dur = generate_trajectory((100, 200), (800, 450))
        for pt in pts:
            set_cursor_pos(*pt.as_tuple())
            sleep(sample_interval_ms / 1000)
    """
    if config is None:
        config = TrajectoryConfig()

    if isinstance(start, tuple):
        start = Point(*start)
    if isinstance(end, tuple):
        end = Point(*end)

    distance = start.distance(end)
    duration_ms = _fitts_duration(distance, target_width, config)

    # Determine number of sample points
    num_samples = max(5, int(distance * config.steps_per_px))
    sample_interval_ms = duration_ms / num_samples

    # Generate Bezier control points
    p1, p2 = _generate_control_points(start, end, config)

    # Generate spatial positions along the Bezier at uniform t
    spatial_points = [
        _evaluate_cubic_bezier(start, p1, p2, end, i / (num_samples - 1))
        for i in range(num_samples)
    ]

    # Apply minimum-jerk velocity profile to distribute points temporally
    temporal_offsets = [_minimum_jerk_profile(i / (num_samples - 1)) for i in range(num_samples)]

    # Remap spatial points to temporal distribution
    # First compute cumulative arc-lengths
    arc_lengths = [0.0]
    for i in range(1, len(spatial_points)):
        arc_lengths.append(arc_lengths[-1] + spatial_points[i].distance(spatial_points[i - 1]))
    total_arc = arc_lengths[-1] or 1.0

    # Normalize arc-lengths to [0, 1]
    arc_norm = [l / total_arc for l in arc_lengths]

    # For each temporal offset, find the corresponding spatial position
    result: list[Point] = []
    for s_target in temporal_offsets:
        # Binary-search arc_norm for the closest value to s_target
        idx = _find_arc_index(arc_norm, s_target)
        if idx >= len(spatial_points) - 1:
            result.append(spatial_points[-1])
        else:
            # Linear interpolation between arc_norm[idx] and arc_norm[idx+1]
            a0, a1 = arc_norm[idx], arc_norm[idx + 1]
            frac = 0.0 if a1 == a0 else (s_target - a0) / (a1 - a0)
            frac = max(0.0, min(1.0, frac))
            result.append(spatial_points[idx].lerp(spatial_points[idx + 1], frac))

    # Overshoot simulation
    if distance > 800 and random.random() < config.overshoot_chance:
        overshoot_dist = distance * random.uniform(0.02, config.overshoot_fraction)
        overshoot_dx = (end.x - start.x) / distance * overshoot_dist
        overshoot_dy = (end.y - start.y) / distance * overshoot_dist
        overshoot_point = Point(end.x + overshoot_dx, end.y + overshoot_dy)

        # Add a few overshoot points
        n_overshoot = max(2, int(num_samples * config.overshoot_correction_ratio))
        for i in range(n_overshoot):
            t = (i + 1) / n_overshoot
            result.append(end.lerp(overshoot_point, t))

        # Correction back to target
        n_correct = max(2, int(n_overshoot * 0.6))
        for i in range(n_correct):
            t = (i + 1) / n_correct
            result.append(overshoot_point.lerp(end, t))

    return result, duration_ms


def _find_arc_index(arc_norm: list[float], target: float) -> int:
    """Binary search for the closest arc-length index ≤ target."""
    lo, hi = 0, len(arc_norm) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if arc_norm[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def generate_micro_drift(
    anchor: Point, duration_ms: float, config: TrajectoryConfig | None = None
) -> list[Point]:
    """Generate micro-drift movements — tiny random wanderings while "stationary".

    Humans never hold perfectly still; this simulates the natural sub-pixel drift.
    """
    if config is None:
        config = TrajectoryConfig()

    num_samples = max(2, int(duration_ms / 100))  # one sample every ~100ms
    points = []
    cx, cy = anchor.x, anchor.y
    for _ in range(num_samples):
        cx += random.gauss(0, config.micro_drift_speed * 0.1)
        cy += random.gauss(0, config.micro_drift_speed * 0.1)
        points.append(Point(cx, cy))
    return points

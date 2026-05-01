"""
Fitts' Law timing and human reaction-delay simulation.

Real humans don't react instantly. This module provides:

1. Fitts' Law duration prediction (shared with trajectory.py)
2. Reaction delays — cognitive processing time before an action
3. Micro-pauses — sub-second pauses within a movement sequence
4. Action transitions — delays between different actions (click → type, etc.)

Reference: Card, Moran & Newell (1983), "The Psychology of Human-Computer Interaction"
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class TimingConfig:
    # Cognitive reaction time (ms) — seeing something and deciding to act
    reaction_min_ms: float = 100.0
    reaction_max_ms: float = 350.0

    # Micro-pause probability and duration (within a movement)
    micro_pause_chance: float = 0.15
    micro_pause_min_ms: float = 30.0
    micro_pause_max_ms: float = 120.0

    # Action transition delay (between different actions)
    # e.g., mouse click → start typing
    transition_min_ms: float = 150.0
    transition_max_ms: float = 600.0

    # Fitts' Law coefficients
    fitts_a: float = 50.0
    fitts_b: float = 120.0

    # Reading / scanning delay — time to "read" a page before acting
    scan_min_ms: float = 200.0
    scan_max_ms: float = 1500.0

    # Click duration — how long a button is held down (ms)
    click_min_ms: float = 40.0
    click_max_ms: float = 120.0

    # Double-click interval (ms between two clicks)
    double_click_min_ms: float = 120.0
    double_click_max_ms: float = 300.0


# Global instance so multiple modules share consistent defaults
_default_config = TimingConfig()


def fitts_duration(
    distance_px: float, target_width_px: float, config: TimingConfig | None = None
) -> float:
    """Predict movement duration using Fitts' Law (milliseconds)."""
    if config is None:
        config = _default_config
    if target_width_px <= 0:
        target_width_px = 1.0
    id_ = math.log2(distance_px / target_width_px + 1)
    return config.fitts_a + config.fitts_b * id_


def reaction_delay(config: TimingConfig | None = None) -> float:
    """Random cognitive reaction delay (seconds)."""
    if config is None:
        config = _default_config
    return random.uniform(config.reaction_min_ms, config.reaction_max_ms) / 1000.0


def micro_pause(config: TimingConfig | None = None) -> float:
    """Potentially a micro-pause. Returns seconds (possibly 0)."""
    if config is None:
        config = _default_config
    if random.random() > config.micro_pause_chance:
        return 0.0
    return random.uniform(config.micro_pause_min_ms, config.micro_pause_max_ms) / 1000.0


def transition_delay(config: TimingConfig | None = None) -> float:
    """Delay between different action types (seconds)."""
    if config is None:
        config = _default_config
    return random.uniform(config.transition_min_ms, config.transition_max_ms) / 1000.0


def scan_delay(config: TimingConfig | None = None) -> float:
    """Delay for "reading/scanning" a page before acting (seconds)."""
    if config is None:
        config = _default_config
    return random.uniform(config.scan_min_ms, config.scan_max_ms) / 1000.0


def click_duration(config: TimingConfig | None = None) -> float:
    """Duration of a mouse button press (seconds)."""
    if config is None:
        config = _default_config
    return random.uniform(config.click_min_ms, config.click_max_ms) / 1000.0


def double_click_interval(config: TimingConfig | None = None) -> float:
    """Interval between two clicks in a double-click (seconds)."""
    if config is None:
        config = _default_config
    return random.uniform(config.double_click_min_ms, config.double_click_max_ms) / 1000.0

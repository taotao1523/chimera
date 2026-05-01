"""
Win32 hardware mouse control — the "hands" of Chimera.

Uses `SetCursorPos` + `SendInput` to generate real OS-level mouse events
that are indistinguishable from human input at the browser/application level.

Key distinction from CDP-level automation:
- CDP `Input.dispatchMouseEvent` → browser sees a synthetic event
- Win32 `SendInput` → OS injects a hardware event; browser cannot tell the difference
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

from chimera.humanize.timing import (
    TimingConfig,
    click_duration,
    double_click_interval,
    micro_pause,
    reaction_delay,
    transition_delay,
)
from chimera.humanize.trajectory import (
    Point,
    TrajectoryConfig,
    generate_micro_drift,
    generate_trajectory,
)
from chimera.humanize.tremor import TremorConfig, apply_tremor

# ═══════════════════════════════════════════════════════════════
# Win32 API declarations
# ═══════════════════════════════════════════════════════════════

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# Input type constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

# DPI awareness
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class WIN32_INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


# Set DPI awareness so coordinates aren't virtualised
try:
    _user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except Exception:
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


@dataclass
class MouseConfig:
    trajectory: TrajectoryConfig | None = None
    tremor: TremorConfig | None = None
    timing: TimingConfig | None = None


def get_cursor_pos() -> Point:
    """Get current cursor position in screen coordinates."""
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return Point(float(pt.x), float(pt.y))


def set_cursor_pos(x: int, y: int) -> None:
    """Directly set cursor position (no animation)."""
    _user32.SetCursorPos(int(x), int(y))


def move_to(
    x: float,
    y: float,
    target_width: float = 20.0,
    config: MouseConfig | None = None,
) -> float:
    """Move the mouse to (x, y) with a human-like trajectory.

    Generates a full Bezier trajectory with tremor, then replays it
    through `SetCursorPos` at the appropriate frame rate.

    Returns:
        Total duration of the movement in seconds (for caller awareness).
    """
    if config is None:
        config = MouseConfig()

    start = get_cursor_pos()
    end = Point(x, y)

    pts, duration_ms = generate_trajectory(
        start, end, target_width, config.trajectory or TrajectoryConfig()
    )

    # Apply tremor if enabled
    tcfg = config.tremor or TremorConfig()
    pts = apply_tremor(pts, duration_ms, tcfg)

    sample_interval = duration_ms / 1000.0 / len(pts)

    for pt in pts:
        set_cursor_pos(int(pt.x), int(pt.y))
        time.sleep(sample_interval)

        # Random micro-pause
        time.sleep(micro_pause(config.timing))

    return duration_ms / 1000.0


def click(
    button: str = "left",
    config: MouseConfig | None = None,
) -> None:
    """Perform a mouse click at the current cursor position.

    The click is a real hardware event with realistic press/release timing.

    Args:
        button: "left", "right", or "middle"
    """
    down_flags = {
        "left": MOUSEEVENTF_LEFTDOWN,
        "right": MOUSEEVENTF_RIGHTDOWN,
        "middle": MOUSEEVENTF_MIDDLEDOWN,
    }
    up_flags = {
        "left": MOUSEEVENTF_LEFTUP,
        "right": MOUSEEVENTF_RIGHTUP,
        "middle": MOUSEEVENTF_MIDDLEUP,
    }

    down_flag = down_flags[button]
    up_flag = up_flags[button]
    hold_duration = click_duration(config.timing if config else None)

    _send_mouse_input(0, 0, 0, down_flag)
    time.sleep(hold_duration)
    _send_mouse_input(0, 0, 0, up_flag)


def double_click(
    button: str = "left",
    config: MouseConfig | None = None,
) -> None:
    """Double-click at current cursor position with realistic inter-click interval."""
    click(button, config)
    time.sleep(double_click_interval(config.timing if config else None))
    click(button, config)


def click_and_move(
    x: float,
    y: float,
    target_width: float = 20.0,
    button: str = "left",
    config: MouseConfig | None = None,
) -> float:
    """Combined move + click — the most common human interaction pattern.

    Moves cursor to (x, y) with full human trajectory, pauses briefly
    (reaction), then clicks.

    Returns:
        Total duration in seconds.
    """
    dur = move_to(x, y, target_width, config)
    time.sleep(reaction_delay(config.timing if config else None))
    click(button, config)
    return dur


def scroll(
    amount: int,
    config: MouseConfig | None = None,
) -> None:
    """Scroll the mouse wheel.

    Positive amount = scroll up, negative = scroll down.
    Each unit corresponds to `WHEEL_DELTA` (120), i.e. one "notch".

    For human-like scrolling, this breaks large scrolls into multiple
    smaller notches with slight delays.
    """
    notch_size = 120
    notches = abs(amount)

    for _ in range(notches):
        delta = notch_size if amount > 0 else -notch_size
        _send_mouse_input(0, 0, delta, MOUSEEVENTF_WHEEL)
        time.sleep(micro_pause(config.timing if config else None))


def hover(x: float, y: float, duration_ms: float = 500.0, config: MouseConfig | None = None) -> None:
    """Move to a position and hover there with micro-drift."""
    move_to(x, y, config=config)

    tcfg = config.trajectory if config else TrajectoryConfig()
    drifts = generate_micro_drift(Point(x, y), duration_ms, tcfg)
    interval = duration_ms / 1000.0 / len(drifts) if drifts else 0
    for pt in drifts:
        set_cursor_pos(int(pt.x), int(pt.y))
        time.sleep(interval)


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════


def _send_mouse_input(dx: int, dy: int, mouse_data: int, flags: int) -> None:
    """Send a single mouse input event via Win32 SendInput."""
    inp = WIN32_INPUT()
    inp.type = INPUT_MOUSE
    inp.mi.dx = dx
    inp.mi.dy = dy
    inp.mi.mouseData = mouse_data
    inp.mi.dwFlags = flags
    inp.mi.time = 0
    inp.mi.dwExtraInfo = None

    result = _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if result == 0:
        raise ctypes.WinError(ctypes.get_last_error())


def send_mouse_down(button: str = "left") -> None:
    """Send a raw mouse-down event (for use by the bridge layer)."""
    flags = {
        "left": MOUSEEVENTF_LEFTDOWN,
        "right": MOUSEEVENTF_RIGHTDOWN,
        "middle": MOUSEEVENTF_MIDDLEDOWN,
    }
    _send_mouse_input(0, 0, 0, flags[button])


def send_mouse_up(button: str = "left") -> None:
    """Send a raw mouse-up event (for use by the bridge layer)."""
    flags = {
        "left": MOUSEEVENTF_LEFTUP,
        "right": MOUSEEVENTF_RIGHTUP,
        "middle": MOUSEEVENTF_MIDDLEUP,
    }
    _send_mouse_input(0, 0, 0, flags[button])


def drag(start_x: float, start_y: float, end_x: float, end_y: float, config: MouseConfig | None = None) -> float:
    """Perform a drag operation (mouse down → move → mouse up)."""
    move_to(start_x, start_y, config=config)
    time.sleep(reaction_delay(config.timing if config else None))
    send_mouse_down("left")
    time.sleep(micro_pause(config.timing if config else None))
    dur = move_to(end_x, end_y, config=config)
    time.sleep(micro_pause(config.timing if config else None))
    send_mouse_up("left")
    return dur

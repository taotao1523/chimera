"""
Win32 hardware keyboard control.

Uses `SendInput` to inject real OS-level keyboard events. The browser
sees `KeyboardEvent.isTrusted === true` with realistic timings.

Supports:
- Character-by-character typing with variable inter-key intervals
- Shift modifier for uppercase and symbols
- Special keys (Enter, Tab, Escape, Backspace, arrows)
- Key combinations (Ctrl+C, Ctrl+V, etc.)
- Occasional typos with backspace correction (human imperfection)
"""

from __future__ import annotations

import ctypes
import random
import time
from ctypes import wintypes
from dataclasses import dataclass

from chimera.humanize.timing import TimingConfig, micro_pause

# ═══════════════════════════════════════════════════════════════
# Win32 API
# ═══════════════════════════════════════════════════════════════

_user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# Virtual key codes (subset)
VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "clear": 0x0C,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "caps_lock": 0x14,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "print_screen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

# Characters that require Shift
_SHIFT_CHARS = set('~!@#$%^&*()_+{}|:"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ')

# Reusable ctypes structures (set up once)
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _WIN32_INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


# ═══════════════════════════════════════════════════════════════
# Typing config
# ═══════════════════════════════════════════════════════════════


@dataclass
class KeyboardConfig:
    # Inter-key interval range (seconds)
    # Average human typing speed: ~40-60 WPM → 200-300ms per character
    interval_min: float = 0.06
    interval_max: float = 0.22

    # Longer pauses at word boundaries (on space)
    word_boundary_pause_min: float = 0.15
    word_boundary_pause_max: float = 0.40

    # Long pause before starting to type (seconds)
    initial_pause_min: float = 0.3
    initial_pause_max: float = 1.2

    # Typo rate (probability per character)
    typo_rate: float = 0.015  # ~1.5% typo rate

    # Timing config reference
    timing: TimingConfig | None = None


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def type_text(
    text: str,
    config: KeyboardConfig | None = None,
) -> float:
    """Type a string with human-like rhythm.

    Characters are typed one by one with variable delays.
    Occasional typos are made and corrected via backspace.

    Returns:
        Total duration in seconds.
    """
    if config is None:
        config = KeyboardConfig()

    total_duration = 0.0

    # Initial pause — "finding the keyboard" / reading the target
    initial_pause = random.uniform(config.initial_pause_min, config.initial_pause_max)
    time.sleep(initial_pause)
    total_duration += initial_pause

    i = 0
    while i < len(text):
        ch = text[i]
        delay = random.uniform(config.interval_min, config.interval_max)

        # Word boundary pause
        if ch == " ":
            delay = random.uniform(config.word_boundary_pause_min, config.word_boundary_pause_max)

        time.sleep(delay)
        total_duration += delay

        # Typo simulation
        if config.typo_rate > 0 and ch.isalpha() and random.random() < config.typo_rate:
            # Type a wrong adjacent key, then backspace, then correct
            wrong_ch = _adjacent_key(ch)
            if wrong_ch != ch:
                _send_character(wrong_ch)
                time.sleep(random.uniform(0.05, 0.15))
                _send_vk(VK["backspace"])
                time.sleep(random.uniform(0.05, 0.15))
                total_duration += 0.3  # approximate

        _send_character(ch)
        i += 1

    return total_duration


def press_key(key: str) -> None:
    """Press and release a single named key.

    Args:
        key: Key name, e.g., "enter", "tab", "escape", "f5", "left"
    """
    vk = VK.get(key.lower())
    if vk is None:
        raise ValueError(f"Unknown key: {key}. Known: {sorted(VK)}")
    _send_vk(vk)


def press_combination(*keys: str) -> None:
    """Press a key combination like Ctrl+C, Ctrl+V, Alt+F4, etc.

    Keys are held down in order, then released in reverse order.

    Example:
        press_combination("ctrl", "c")
        press_combination("ctrl", "shift", "t")
    """
    vks = []
    for k in keys:
        vk = VK.get(k.lower())
        if vk is None:
            raise ValueError(f"Unknown key: {k}")
        vks.append(vk)

    # Press down in order
    for vk in vks:
        _send_key_event(vk, KEYEVENTF_KEYDOWN)
        time.sleep(random.uniform(0.02, 0.06))

    time.sleep(random.uniform(0.03, 0.08))

    # Release in reverse order
    for vk in reversed(vks):
        _send_key_event(vk, KEYEVENTF_KEYUP)
        time.sleep(random.uniform(0.02, 0.06))


def type_file_path(path: str, config: KeyboardConfig | None = None) -> float:
    """Type a file path — faster than normal text (muscle memory).

    File paths use different timing because users type them from
    muscle memory (faster, more confident).
    """
    if config is None:
        config = KeyboardConfig()
    modified = KeyboardConfig(
        interval_min=config.interval_min * 0.6,
        interval_max=config.interval_max * 0.6,
        word_boundary_pause_min=0.05,
        word_boundary_pause_max=0.15,
        initial_pause_min=config.initial_pause_min * 0.5,
        initial_pause_max=config.initial_pause_max * 0.5,
        typo_rate=0.0,  # file paths are rarely mistyped
        timing=config.timing,
    )
    return type_text(path, modified)


# ═══════════════════════════════════════════════════════════════
# Internal
# ═══════════════════════════════════════════════════════════════


def _send_key_event(vk: int, flags: int) -> None:
    """Send a single keyboard input event."""
    inp = _WIN32_INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = 0
    inp.ki.dwFlags = flags
    inp.ki.time = 0
    inp.ki.dwExtraInfo = None

    result = _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if result == 0:
        raise ctypes.WinError(ctypes.get_last_error())


def _send_vk(vk: int) -> None:
    """Press and release a virtual key."""
    _send_key_event(vk, KEYEVENTF_KEYDOWN)
    time.sleep(random.uniform(0.03, 0.08))
    _send_key_event(vk, KEYEVENTF_KEYUP)


def _send_character(ch: str) -> None:
    """Type a single character, handling Shift where needed."""
    needs_shift = ch in _SHIFT_CHARS
    vk = _char_to_vk(ch.lower() if needs_shift else ch)

    if needs_shift:
        _send_key_event(VK["shift"], KEYEVENTF_KEYDOWN)
        time.sleep(random.uniform(0.01, 0.03))

    _send_vk(vk)

    if needs_shift:
        time.sleep(random.uniform(0.01, 0.03))
        _send_key_event(VK["shift"], KEYEVENTF_KEYUP)


def _char_to_vk(ch: str) -> int:
    """Convert a character to its virtual key code."""
    result = _user32.VkKeyScanW(ord(ch))
    if result == -1:
        raise ValueError(f"Cannot map character to virtual key: {ch!r}")
    return result & 0xFF


def _adjacent_key(ch: str) -> str:
    """Return a nearby key on a QWERTY keyboard for typo simulation."""
    keyboard_rows = [
        "1234567890-=",
        "qwertyuiop[]",
        "asdfghjkl;'",
        "zxcvbnm,./",
    ]
    ch_lower = ch.lower()
    for row in keyboard_rows:
        if ch_lower in row:
            idx = row.index(ch_lower)
            offset = random.choice([-1, 1])
            new_idx = max(0, min(len(row) - 1, idx + offset))
            adjacent = row[new_idx]
            return adjacent.upper() if ch.isupper() else adjacent
    return ch

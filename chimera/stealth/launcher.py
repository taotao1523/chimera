"""
Chrome launcher with stealth configuration.

Every flag and preference is chosen to minimise the browser's
"automation fingerprint". This is not C++-level patching (like Camoufox),
but it eliminates the common CDP/Chrome tells that basic scripts miss.

Key decisions:
- Use --remote-debugging-pipe (not --port) to avoid a listening TCP socket
- Disable "Chrome is being controlled" infobars
- Remove blink automation features
- Normalise WebGL/Canvas/AudioContext not via JS but via CDP pre-load scripts
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# Flags that suppress automation indicators in Chrome
# Note: debug transport (--remote-debugging-pipe or --remote-debugging-port)
# is added dynamically in launch() based on the chosen debug_mode.
_STEALTH_ARGS = [
    # ── Core stealth ────────────────────────────────
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-default-apps",

    # ── Suppress automation infobars ────────────────
    "--disable-blink-features=AutomationControlled",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--disable-infobars",

    # ── WebDriver / automation flags removal ───────
    "--disable-dev-shm-usage",

    # ── Extensions ─────────────────────────────────
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",

    # ── Popups / prompts ───────────────────────────
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",

    # ── Misc noise reduction ───────────────────────
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--disable-field-trial-config",
    "--disable-hang-monitor",
    "--disable-client-side-phishing-detection",

    # ── Sandbox (keep on for safety) ───────────────
    # "--no-sandbox"  # only if running as admin / in Docker
]

_STEALTH_PREFERENCES = {
    # Disable "Save password?" prompts
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False,

    # Don't offer translations
    "translate.enabled": False,

    # Disable various "smart" features that expose automation
    "browser.disable_augmented_smart_lock": True,
    "browser.enable_automatic_password_saving": False,

    # Normalize plugins / MIME handlers
    "plugins.always_open_pdf_externally": False,

    # Disable safe-browsing noise (requests to Google)
    "safebrowsing.enabled": False,

    # Hide "restore pages" prompt
    "profile.exit_type": "Normal",

    # Disable "chrome is out of date" checks
    "browser.check_default_browser": False,
    "browser.disable_last_session_restore": True,

    # Normalise WebRTC (don't leak local IPs)
    "webrtc.ip_handling_policy": "disable_non_proxied_udp",
    "webrtc.multiple_routes_enabled": False,
    "webrtc.nonproxied_udp_enabled": False,
}


class ChromeLauncher:
    """Launch and configure a Chrome instance for stealth operation."""

    def __init__(
        self,
        chrome_path: str | None = None,
        user_data_dir: str | Path | None = None,
        extra_args: Sequence[str] | None = None,
        extra_prefs: dict | None = None,
    ):
        self._chrome_path = self._resolve_chrome_path(chrome_path)
        self._user_data_dir = user_data_dir or self._default_user_data_dir()
        self._extra_args = extra_args or []
        self._extra_prefs = extra_prefs or {}
        self._process: subprocess.Popen | None = None

    # ── Public API ──────────────────────────────────────────

    def launch(
        self,
        headless: bool = False,
        url: str | None = None,
        debug_mode: str = "pipe",
        debug_port: int = 9222,
    ) -> subprocess.Popen:
        """Launch Chrome with stealth flags.

        Args:
            headless: If True, run in headless mode. NOT recommended for anti-detection.
            url: Optional URL to open on launch.
            debug_mode: "pipe" (default, stealthier) or "port" (easier to debug).
                Pipe mode communicates via stdin/stdout with no TCP listener.
                Port mode opens a TCP debug port (requires --user-data-dir to work).
            debug_port: TCP port for debug_mode="port". Default 9222.
        """
        if debug_mode not in ("pipe", "port"):
            raise ValueError(f"debug_mode must be 'pipe' or 'port', got {debug_mode!r}")

        # Clear stale lock files from a previous unclean shutdown
        self._clear_lock_files()

        args = [self._chrome_path]

        # Debug transport — must come BEFORE --user-data-dir
        if debug_mode == "pipe":
            args.append("--remote-debugging-pipe")
        else:
            args.append(f"--remote-debugging-port={debug_port}")

        # Stealth flags (general anti-detection)
        args.extend(_STEALTH_ARGS)

        # User data dir — critical: without this, Chrome ignores debug flags
        # and joins an existing session, defeating both pipe and port modes.
        args.append(f"--user-data-dir={self._user_data_dir}")

        # Write Preferences to user_data_dir before launch
        self._write_prefs()

        # Headless (use with caution — detectable)
        if headless:
            args.append("--headless=new")
            args.append("--window-size=1920,1080")

        # Extra user args
        args.extend(self._extra_args)

        # URL to open
        if url:
            args.append(url)

        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return self._process

    def terminate(self) -> None:
        """Kill the Chrome process."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ── Internals ───────────────────────────────────────────

    @staticmethod
    def _resolve_chrome_path(given: str | None) -> str:
        if given:
            return given

        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
        elif sys.platform == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        else:
            candidates = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
            ]

        for c in candidates:
            if os.path.exists(c):
                return c
        raise FileNotFoundError(
            f"Chrome not found. Checked: {candidates}. "
            f"Specify chrome_path explicitly."
        )

    @staticmethod
    def _default_user_data_dir() -> str:
        return str(Path.home() / ".chimera" / "chrome-profile")

    @staticmethod
    def _clear_lock_files() -> None:
        """Remove stale Chrome lock files that prevent debug flags from taking effect."""
        import glob

        user_data_root = Path.home() / ".chimera" / "chrome-profile"
        lock_patterns = [
            "SingletonLock",
            "SingletonSocket",
            "SingletonCookie",
        ]
        for pattern in lock_patterns:
            for lock in glob.glob(str(user_data_root / pattern)):
                try:
                    os.remove(lock)
                except OSError:
                    pass

    def _write_prefs(self) -> str:
        """Write a Chrome Preferences JSON file and return the path."""
        import json

        prefs = dict(_STEALTH_PREFERENCES)
        prefs.update(self._extra_prefs)

        prefs_dir = Path(self._user_data_dir)
        prefs_dir.mkdir(parents=True, exist_ok=True)

        # Actually, Chrome doesn't use --prefs-file. Instead we write
        # directly to the user_data_dir's Local State / Preferences.
        # The --prefs-file flag doesn't exist; Chrome reads <user_data_dir>/Local State
        # The Preferences file goes in <user_data_dir>/Default/Preferences
        default_dir = prefs_dir / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)

        prefs_path = default_dir / "Preferences"
        # Load existing prefs if any, merge
        existing = {}
        if prefs_path.exists():
            try:
                with open(prefs_path, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(prefs)

        with open(prefs_path, "w") as f:
            json.dump(existing, f, indent=2)

        return str(prefs_path)

    # Not used but kept for debugging
    def _get_prefs_file_arg(self) -> str:
        """Get a fake --prefs-file path for the args list (ignored in practice)."""
        return str(Path(self._user_data_dir) / "Default" / "Preferences")

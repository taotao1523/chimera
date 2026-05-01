"""
Browser lifecycle and window management.

Launches Chrome, establishes the CDP pipe connection, applies stealth
patches, and queries the window position (required for screen coordinate
calculations).
"""

from __future__ import annotations

import asyncio
import ctypes
import time
from contextlib import asynccontextmanager
from ctypes import wintypes
from pathlib import Path
from typing import AsyncIterator, Sequence

from chimera.core.cdp import CDPClient
from chimera.core.dom import DOMClient
from chimera.stealth.launcher import ChromeLauncher
from chimera.stealth.patches import StealthPatcher
from chimera.hardware.mouse import MouseConfig
from chimera.hardware.keyboard import KeyboardConfig


class BrowserWindow:
    """Represents the Chrome window on screen, providing its position."""

    def __init__(self, process_id: int):
        self._pid = process_id
        self._hwnd: int | None = None

    def locate(self, timeout: float = 10.0) -> bool:
        """Find the Chrome window handle by process ID.

        Returns True once the window is visible and has a valid rect.
        """
        import ctypes.wintypes

        _user32 = ctypes.windll.user32

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found_hwnd: list[int] = []

        def enum_callback(hwnd, _lparam):
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self._pid:
                # Check if visible and has Chrome class
                if _user32.IsWindowVisible(hwnd):
                    class_name = ctypes.create_unicode_buffer(256)
                    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
                    cls = class_name.value
                    if cls in ("Chrome_WidgetWin_1", "MozillaWindowClass"):
                        found_hwnd.append(hwnd)
                        return False  # stop enumeration
            return True  # continue

        callback = WNDENUMPROC(enum_callback)
        deadline = time.time() + timeout

        while time.time() < deadline:
            found_hwnd.clear()
            ctypes.windll.user32.EnumWindows(callback, 0)
            if found_hwnd:
                self._hwnd = found_hwnd[0]
                return True
            time.sleep(0.3)

        return False

    def get_rect(self) -> tuple[int, int, int, int]:
        """Get window rect as (left, top, right, bottom).

        Raises RuntimeError if window hasn't been located.
        """
        if self._hwnd is None:
            raise RuntimeError("Window not located. Call locate() first.")

        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    def get_position(self) -> tuple[int, int]:
        """Get window position as (left, top)."""
        l, t, r, b = self.get_rect()
        return (l, t)

    def get_size(self) -> tuple[int, int]:
        """Get window size as (width, height)."""
        l, t, r, b = self.get_rect()
        return (r - l, b - t)

    def focus(self) -> None:
        """Bring the Chrome window to the foreground."""
        if self._hwnd:
            ctypes.windll.user32.SetForegroundWindow(self._hwnd)


class Browser:
    """Manages a Chrome instance with CDP connection and DOM access.

    This is the "body" that houses the CDP eyes and connects to the hardware hands.
    """

    @property
    def cdp(self) -> CDPClient:
        if self._cdp is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._cdp

    @property
    def dom(self) -> DOMClient:
        if self._dom is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._dom

    @property
    def window(self) -> BrowserWindow:
        if self._window is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._window

    @property
    def mouse_config(self) -> MouseConfig:
        return self._mouse_config

    @mouse_config.setter
    def mouse_config(self, cfg: MouseConfig):
        self._mouse_config = cfg

    @property
    def keyboard_config(self) -> KeyboardConfig:
        return self._keyboard_config

    @keyboard_config.setter
    def keyboard_config(self, cfg: KeyboardConfig):
        self._keyboard_config = cfg

    def __init__(
        self,
        chrome_path: str | None = None,
        user_data_dir: str | None = None,
        extra_stealth_args: Sequence[str] | None = None,
        mouse_config: MouseConfig | None = None,
        keyboard_config: KeyboardConfig | None = None,
        debug_mode: str = "pipe",
        debug_port: int = 9222,
    ):
        self._launcher = ChromeLauncher(
            chrome_path=chrome_path,
            user_data_dir=user_data_dir,
            extra_args=extra_stealth_args,
        )
        self._cdp: CDPClient | None = None
        self._dom: DOMClient | None = None
        self._stealth: StealthPatcher | None = None
        self._window: BrowserWindow | None = None
        self._mouse_config = mouse_config or MouseConfig()
        self._keyboard_config = keyboard_config or KeyboardConfig()
        self._stealth_applied = False
        self._debug_mode = debug_mode
        self._debug_port = debug_port

    # ── Lifecycle ──────────────────────────────────────────

    async def launch(
        self,
        url: str | None = None,
        headless: bool = False,
        window_timeout: float = 15.0,
    ) -> None:
        """Launch Chrome and establish CDP connection.

        Args:
            url: Optional URL to navigate to after launch.
            headless: If True, run headless. Discouraged for anti-detection.
            window_timeout: Max seconds to wait for the window to appear.
        """
        # Launch Chrome process with chosen debug mode
        process = self._launcher.launch(
            headless=headless, url=url,
            debug_mode=self._debug_mode, debug_port=self._debug_port,
        )

        # Connect CDP based on mode
        if self._debug_mode == "pipe":
            self._cdp = await CDPClient.from_process(process)
            try:
                await asyncio.wait_for(self._cdp._wait_browser_ready(), timeout=15.0)
            except asyncio.TimeoutError:
                raise RuntimeError("Chrome CDP pipe did not respond in time")
        else:
            # Port mode: wait for HTTP endpoint, then connect via WebSocket
            await self._wait_for_port(self._debug_port, timeout=15.0)
            self._cdp = await CDPClient.connect_port(port=self._debug_port)

        # Attach to a page target
        target_result = await self._cdp.send("Target.getTargets")
        page_targets = [t for t in target_result.get("targetInfos", []) if t.get("type") == "page"]
        if page_targets:
            target_id = page_targets[0]["targetId"]
            await self._cdp.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        else:
            await self._cdp.send("Target.createTarget", {"url": url or "about:blank"})

        # Navigate to URL if given (and if not already there)
        if url and page_targets:
            await self._cdp.send("Page.navigate", {"url": url})

        # Initialize DOM
        self._dom = DOMClient(self._cdp)
        await self._dom.initialize()

        # Apply stealth patches (before any page JS runs for future navigations)
        self._stealth = StealthPatcher(self._cdp)
        await self._stealth.apply_all()
        self._stealth_applied = True

        # Locate window
        if not headless:
            self._window = BrowserWindow(process.pid)
            if not self._window.locate(timeout=window_timeout):
                raise RuntimeError(
                    "Could not find Chrome window. Is it visible?"
                )
            wx, wy = self._window.get_position()
            self._dom.set_window_offset(wx, wy)

    @staticmethod
    async def _wait_for_port(port: int, timeout: float = 15.0) -> None:
        """Wait for Chrome's debug HTTP endpoint to become available."""
        import time
        import urllib.request

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2
                )
                return
            except Exception:
                await asyncio.sleep(0.3)
        raise TimeoutError(f"Chrome debug port {port} did not open in {timeout}s")

    async def navigate(self, url: str) -> None:
        """Navigate to a new URL."""
        await self.cdp.send("Page.navigate", {"url": url})
        await asyncio.sleep(0.5)
        # Reinitialize DOM for new page
        if self._dom:
            await self._dom.initialize()
            # Apply stealth to new document
            if self._stealth:
                await self._stealth.apply_all()
            # Update window offset (window might have moved)
            if self._window:
                wx, wy = self._window.get_position()
                self._dom.set_window_offset(wx, wy)

    async def close(self) -> None:
        """Shut down the browser."""
        if self._cdp:
            await self._cdp.close()
        self._launcher.terminate()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

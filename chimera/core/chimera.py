"""
Chimera — the main orchestration class.

Composes CDP perception (eyes) with Win32 hardware input (hands)
through a bridge that translates DOM elements into real mouse/keyboard actions.

This is the only class users need to import.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

from chimera.core.cdp import CDPClient
from chimera.core.dom import DOMClient, Element, DOMError
from chimera.core.browser import Browser, BrowserWindow
from chimera.hardware.mouse import (
    MouseConfig,
    click,
    click_and_move,
    double_click,
    drag,
    get_cursor_pos,
    hover,
    move_to,
    scroll,
)
from chimera.hardware.keyboard import (
    KeyboardConfig,
    press_combination,
    press_key,
    type_text,
    type_file_path,
)
from chimera.humanize.timing import (
    TimingConfig,
    micro_pause,
    reaction_delay,
    scan_delay,
    transition_delay,
)


class Chimera:
    """CDP eyes + Win32 hands — the undetectable browser.

    Usage:
        async with Chimera.launch("https://example.com") as c:
            await c.click_text("Login")
            await c.type_into("input[name='user']", "myusername")
            await c.type_into("input[name='pass']", "mypassword")
            await c.click_text("Sign in")
    """

    def __init__(
        self,
        browser: Browser,
        mouse_config: MouseConfig | None = None,
        keyboard_config: KeyboardConfig | None = None,
        timing_config: TimingConfig | None = None,
    ):
        self._browser = browser
        self._cdp = browser.cdp
        self._dom = browser.dom
        self._window = browser.window
        self._mouse_cfg = mouse_config or MouseConfig()
        self._keyboard_cfg = keyboard_config or KeyboardConfig()
        self._timing_cfg = timing_config or TimingConfig()

    # ═══════════════════════════════════════════════════════
    # Factory
    # ═══════════════════════════════════════════════════════

    @classmethod
    async def launch(
        cls,
        url: str | None = None,
        *,
        chrome_path: str | None = None,
        user_data_dir: str | None = None,
        headless: bool = False,
        debug_mode: str = "pipe",
        debug_port: int = 9222,
        mouse_config: MouseConfig | None = None,
        keyboard_config: KeyboardConfig | None = None,
        timing_config: TimingConfig | None = None,
    ) -> Chimera:
        """Launch Chrome and return a ready-to-use Chimera instance.

        Args:
            url: URL to open (optional).
            chrome_path: Path to Chrome/Chromium executable.
            user_data_dir: Custom user data directory.
            headless: Run headless (not recommended for anti-detection).
            debug_mode: "pipe" (stealth, default) or "port" (for debugging).
            debug_port: TCP port for debug_mode="port". Default 9222.
            mouse_config: Customize human-like mouse behavior.
            keyboard_config: Customize human-like keyboard behavior.
            timing_config: Customize human timing behavior.
        """
        browser = Browser(
            chrome_path=chrome_path,
            user_data_dir=user_data_dir,
            mouse_config=mouse_config,
            keyboard_config=keyboard_config,
            debug_mode=debug_mode,
            debug_port=debug_port,
        )
        await browser.launch(url=url, headless=headless)
        return cls(browser, mouse_config, keyboard_config, timing_config)

    @classmethod
    async def attach(
        cls,
        host: str = "127.0.0.1",
        port: int = 9222,
        url_pattern: str | None = None,
    ) -> Chimera:
        """Attach to an already-running Chrome with --remote-debugging-port.

        Args:
            host: Chrome debug server host.
            port: Chrome debug server port.
            url_pattern: Attach to the first page whose URL contains this string.
        """
        cdp = await CDPClient.connect_port_specific(host, port, url_pattern)
        dom = DOMClient(cdp)
        await dom.initialize()

        # For attached mode, we need to find the window
        # Use a lightweight Browser wrapper
        browser = Browser.__new__(Browser)
        browser._cdp = cdp
        browser._dom = dom

        return cls(browser)

    async def close(self) -> None:
        await self._browser.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ═══════════════════════════════════════════════════════
    # Navigation
    # ═══════════════════════════════════════════════════════

    async def goto(self, url: str) -> None:
        """Navigate to a URL."""
        await self._cdp.send("Page.navigate", {"url": url})
        await asyncio.sleep(0.5)
        await self._dom.initialize()

    async def go_back(self) -> None:
        await self._cdp.send("Page.navigateToHistoryEntry", {"direction": "back"})

    async def go_forward(self) -> None:
        await self._cdp.send("Page.navigateToHistoryEntry", {"direction": "forward"})

    async def reload(self) -> None:
        await self._cdp.send("Page.reload")

    @property
    async def url(self) -> str:
        state = await self._dom.get_page_state()
        return state.get("url", "")

    @property
    async def title(self) -> str:
        return await self._dom.get_title()

    # ═══════════════════════════════════════════════════════
    # Click
    # ═══════════════════════════════════════════════════════

    async def click(
        self,
        selector: str,
        scroll_first: bool = True,
    ) -> float:
        """Click an element by CSS selector.

        The full pipeline: find element → scroll into view → compute screen
        coordinates → human-like mouse movement → hardware-level click.

        Returns total duration in seconds.
        """
        if scroll_first:
            await self._dom.scroll_into_view(selector)
            await asyncio.sleep(0.15)  # let the scroll settle

        el = await self._dom.find_element(selector)
        cx, cy = el.center
        return click_and_move(cx, cy, target_width=el.width, config=self._mouse_cfg)

    async def click_text(
        self,
        text: str,
        exact: bool = False,
    ) -> float:
        """Find an element by visible text and click it.

        This is often more robust than CSS selectors for captcha/anti-bot
        scenarios where selectors are obfuscated.
        """
        el = await self._dom.find_by_text(text, exact=exact)
        cx, cy = el.center
        return click_and_move(cx, cy, target_width=el.width, config=self._mouse_cfg)

    async def double_click(self, selector: str) -> float:
        """Double-click an element."""
        el = await self._dom.find_element(selector)
        cx, cy = el.center
        dur = move_to(cx, cy, target_width=el.width, config=self._mouse_cfg)
        time.sleep(reaction_delay(self._timing_cfg))
        double_click(config=self._mouse_cfg)
        return dur

    # ═══════════════════════════════════════════════════════
    # Type
    # ═══════════════════════════════════════════════════════

    async def type_into(
        self,
        selector: str,
        text: str,
        click_first: bool = True,
        clear_first: bool = False,
    ) -> float:
        """Type text into an input field.

        1. Find the field
        2. Click into it (human-like move + click)
        3. Optionally clear existing content
        4. Type text with human-like rhythm

        Returns total duration in seconds.
        """
        el = await self._dom.find_element(selector)
        cx, cy = el.center

        dur = click_and_move(cx, cy, target_width=el.width, config=self._mouse_cfg)
        time.sleep(transition_delay(self._timing_cfg))

        if clear_first:
            # Select all and delete
            press_combination("ctrl", "a")
            time.sleep(micro_pause(self._timing_cfg))
            press_key("backspace")
            time.sleep(micro_pause(self._timing_cfg))

        dur += type_text(text, config=self._keyboard_cfg)
        return dur

    async def type_into_field(
        self,
        selector: str,
        text: str,
        submit: bool = False,
    ) -> float:
        """Type into a field and optionally press Enter."""
        dur = await self.type_into(selector, text)
        if submit:
            time.sleep(micro_pause(self._timing_cfg))
            press_key("enter")
        return dur

    # ═══════════════════════════════════════════════════════
    # Scroll
    # ═══════════════════════════════════════════════════════

    async def scroll_down(self, amount: int = 300) -> None:
        """Scroll the page down using the mouse wheel."""
        await self._dom.scroll_by(0, amount)
        # Also do a hardware scroll for realism
        time.sleep(0.1)
        scroll(-amount, config=self._mouse_cfg)

    async def scroll_up(self, amount: int = 300) -> None:
        await self._dom.scroll_by(0, -amount)
        time.sleep(0.1)
        scroll(amount, config=self._mouse_cfg)

    async def scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the page."""
        js = "document.documentElement.scrollHeight"
        result = await self._cdp.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        height = result.get("result", {}).get("value", 0)
        await self._dom.scroll_to(0, height)

    async def scroll_to_top(self) -> None:
        await self._dom.scroll_to(0, 0)

    # ═══════════════════════════════════════════════════════
    # Read
    # ═══════════════════════════════════════════════════════

    async def text(self) -> str:
        """Get the visible text content of the page."""
        return await self._dom.get_text_content()

    async def html(self) -> str:
        """Get the full HTML of the page."""
        return await self._dom.get_html()

    async def eval(self, expression: str) -> Any:
        """Execute JavaScript in the page and return the result."""
        return await self._dom.eval(expression)

    async def screenshot(self, path: str | None = None) -> bytes:
        """Capture a screenshot of the current viewport."""
        return await self._dom.screenshot(path)

    async def get_input_fields(self) -> list[dict]:
        """Get a list of all visible input fields with their labels."""
        return await self._dom.get_input_fields()

    # ═══════════════════════════════════════════════════════
    # Form helpers
    # ═══════════════════════════════════════════════════════

    async def fill_form(
        self,
        fields: dict[str, str],
        submit_selector: str | None = None,
    ) -> float:
        """Fill multiple form fields and optionally submit.

        Args:
            fields: Dict mapping CSS selectors to their values.
            submit_selector: Optional selector for the submit button.

        Example:
            await c.fill_form({
                "input[name='email']": "user@example.com",
                "input[name='password']": "secret123",
                "textarea[name='bio']": "Hello world",
            }, submit_selector="button[type=submit]")
        """
        dur = 0.0
        for selector, value in fields.items():
            dur += await self.type_into(selector, value)
            time.sleep(transition_delay(self._timing_cfg))

        if submit_selector:
            dur += await self.click(submit_selector)
        return dur

    # ═══════════════════════════════════════════════════════
    # File upload
    # ═══════════════════════════════════════════════════════

    async def upload_file(
        self,
        selector: str,
        file_path: str,
    ) -> float:
        """Click a file input and type the absolute path."""
        el = await self._dom.find_element(selector)
        cx, cy = el.center
        dur = click_and_move(cx, cy, target_width=el.width, config=self._mouse_cfg)
        time.sleep(transition_delay(self._timing_cfg))

        # Type the file path (muscle memory speed)
        dur += type_file_path(file_path, config=self._keyboard_cfg)
        time.sleep(micro_pause(self._timing_cfg))
        press_key("enter")
        return dur

    # ═══════════════════════════════════════════════════════
    # Drag
    # ═══════════════════════════════════════════════════════

    async def drag_element(
        self,
        from_selector: str,
        to_selector: str,
    ) -> float:
        """Drag one element onto another."""
        src = await self._dom.find_element(from_selector)
        dst = await self._dom.find_element(to_selector)
        src_cx, src_cy = src.center
        dst_cx, dst_cy = dst.center
        return drag(src_cx, src_cy, dst_cx, dst_cy, config=self._mouse_cfg)

    # ═══════════════════════════════════════════════════════
    # Key combos
    # ═══════════════════════════════════════════════════════

    async def copy(self) -> None:
        press_combination("ctrl", "c")

    async def paste(self) -> None:
        press_combination("ctrl", "v")

    async def select_all(self) -> None:
        press_combination("ctrl", "a")

    async def undo(self) -> None:
        press_combination("ctrl", "z")

    # ═══════════════════════════════════════════════════════
    # Window
    # ═══════════════════════════════════════════════════════

    def focus_window(self) -> None:
        """Bring the Chrome window to the foreground."""
        self._window.focus()

    def get_window_rect(self) -> tuple[int, int, int, int]:
        """Get the Chrome window's (left, top, right, bottom)."""
        return self._window.get_rect()

    async def update_window_position(self) -> None:
        """Re-query the window position (if user moved it)."""
        wx, wy = self._window.get_position()
        self._dom.set_window_offset(wx, wy)

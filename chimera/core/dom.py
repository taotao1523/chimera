"""
DOM perception through CDP — the "visual cortex" of Chimera.

Reads the page structure, locates elements, computes their absolute
screen coordinates, and manages scrolling to bring elements into view.

All coordinates returned are absolute screen coordinates, ready for
the Win32 hardware layer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from chimera.core.cdp import CDPClient


@dataclass
class Element:
    """A located DOM element with its screen coordinates."""

    # CDP identifiers
    node_id: int | None = None
    backend_node_id: int | None = None
    object_id: str | None = None

    # Basic descriptors
    tag_name: str = ""
    text: str = ""
    selector_hint: str = ""

    # Bounding box in CSS pixels (relative to viewport)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    # Absolute screen coordinates (accounting for window position, DPI, scroll)
    screen_x: float = 0.0
    screen_y: float = 0.0

    @property
    def center(self) -> tuple[float, float]:
        """Center point of the element in screen coordinates."""
        return (
            self.screen_x + self.width / 2,
            self.screen_y + self.height / 2,
        )

    @property
    def center_screen(self) -> tuple[int, int]:
        """Center point as integer screen coordinates."""
        cx, cy = self.center
        return (int(cx), int(cy))

    def as_dict(self) -> dict:
        return {
            "tag": self.tag_name,
            "text": self.text[:80],
            "selector": self.selector_hint,
            "pos": f"({int(self.screen_x)}, {int(self.screen_y)})",
            "size": f"{int(self.width)}x{int(self.height)}",
        }


class DOMError(Exception):
    pass


class DOMClient:
    """High-level DOM access through CDP.

    This is the "eye" that finds elements and translates them into
    screen coordinates the hardware layer can target.
    """

    def __init__(self, cdp: CDPClient):
        self._cdp = cdp
        self._window_offset_x: float = 0.0
        self._window_offset_y: float = 0.0
        self._content_offset_x: float = 0.0
        self._content_offset_y: float = 0.0
        self._device_pixel_ratio: float = 1.0
        self._page_zoom: float = 1.0

    # ── Initialisation ─────────────────────────────────────

    async def initialize(self) -> None:
        """Enable CDP domains and query viewport geometry."""
        await self._cdp.send("DOM.enable")
        await self._cdp.send("Page.enable")
        await self._cdp.send("Runtime.enable")

        # Get the device pixel ratio for coordinate conversion
        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": "window.devicePixelRatio", "returnByValue": True},
        )
        self._device_pixel_ratio = result.get("result", {}).get("value", 1.0)

        # Get viewport metrics for scroll offset
        layout = await self._cdp.send("Page.getLayoutMetrics")
        visual = layout.get("cssVisualViewport", {})
        self._content_offset_x = visual.get("pageX", 0)
        self._content_offset_y = visual.get("pageY", 0)
        css_size = layout.get("cssLayoutViewport", {})
        self._viewport_width = css_size.get("clientWidth", 1920)
        self._viewport_height = css_size.get("clientHeight", 1080)

    def set_window_offset(self, x: float, y: float) -> None:
        """Set the Chrome window's position on the desktop.

        This is added to viewport coordinates to get absolute screen coords.
        Must be set externally (e.g., via GetWindowRect).
        """
        self._window_offset_x = x
        self._window_offset_y = y

    def set_page_zoom(self, zoom: float) -> None:
        """Set the page zoom level (1.0 = 100%)."""
        self._page_zoom = zoom

    # ── Element location ───────────────────────────────────

    async def find_element(
        self,
        selector: str,
        timeout: float = 10.0,
    ) -> Element:
        """Find a single element by CSS selector.

        Automatically scrolls it into view and computes screen coordinates.
        """
        try:
            result = await self._cdp.send(
                "DOM.getDocument",
                {"depth": -1},
            )
        except Exception:
            raise DOMError("Failed to get document root")

        # Use Runtime.evaluate to query-select the element
        js = f"""
        (function() {{
            const el = document.querySelector({_js_string(selector)});
            if (!el) return null;
            el.scrollIntoView({{behavior: 'instant', block: 'center'}});
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                tagName: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim(),
                visible: el.checkVisibility ? el.checkVisibility() : true,
            }};
        }})()
        """

        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )

        value = result.get("result", {}).get("value")
        if not value:
            raise DOMError(f"Element not found: {selector}")

        el = Element(
            x=value["x"],
            y=value["y"],
            width=value["width"],
            height=value["height"],
            tag_name=value["tagName"],
            text=value.get("text", ""),
            selector_hint=selector,
        )

        self._compute_screen_coords(el)
        return el

    async def find_elements(self, selector: str) -> list[Element]:
        """Find all elements matching a CSS selector."""
        js = f"""
        (function() {{
            const els = document.querySelectorAll({_js_string(selector)});
            return Array.from(els).map(el => {{
                const rect = el.getBoundingClientRect();
                return {{
                    x: rect.x, y: rect.y,
                    width: rect.width, height: rect.height,
                    tagName: el.tagName.toLowerCase(),
                    text: (el.textContent || '').trim(),
                }};
            }});
        }})()
        """

        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )

        values = result.get("result", {}).get("value", [])
        elements = []
        for v in values:
            el = Element(
                x=v["x"], y=v["y"],
                width=v["width"], height=v["height"],
                tag_name=v["tagName"],
                text=v.get("text", ""),
                selector_hint=selector,
            )
            self._compute_screen_coords(el)
            elements.append(el)
        return elements

    async def find_by_text(
        self,
        text: str,
        tag: str | None = None,
        exact: bool = False,
    ) -> Element:
        """Find an element by its visible text content."""
        tag_filter = f"'{tag}'" if tag else "''"
        match_fn = "el.textContent.trim() === text" if exact else "el.textContent.includes(text)"
        js = f"""
        (function() {{
            const text = {_js_string(text)};
            const tag = {tag_filter};
            const candidates = tag ? document.querySelectorAll(tag) : document.querySelectorAll('a,button,input[type=submit],span,p,div,li,td,th,label');
            for (const el of candidates) {{
                if (el.offsetParent === null) continue;
                if ({match_fn}) {{
                    el.scrollIntoView({{behavior: 'instant', block: 'center'}});
                    const rect = el.getBoundingClientRect();
                    return {{
                        x: rect.x, y: rect.y,
                        width: rect.width, height: rect.height,
                        tagName: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim(),
                    }};
                }}
            }}
            return null;
        }})()
        """

        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )

        value = result.get("result", {}).get("value")
        if not value:
            raise DOMError(f"No element found containing text: {text!r}")

        el = Element(
            x=value["x"], y=value["y"],
            width=value["width"], height=value["height"],
            tag_name=value["tagName"],
            text=value.get("text", ""),
            selector_hint=f"text({text})",
        )
        self._compute_screen_coords(el)
        return el

    # ── Page inspection ────────────────────────────────────

    async def get_text_content(self) -> str:
        """Get the visible text content of the page body."""
        result = await self._cdp.send(
            "Runtime.evaluate",
            {
                "expression": "document.body ? document.body.innerText : ''",
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")

    async def get_title(self) -> str:
        """Get the page title."""
        result = await self._cdp.send(
            "Runtime.evaluate",
            {
                "expression": "document.title",
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")

    async def get_html(self) -> str:
        """Get the full page HTML."""
        result = await self._cdp.send(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")

    async def get_page_state(self) -> dict:
        """Get a structured snapshot of the current page state.

        Returns scroll position, viewport size, element count, etc.
        """
        js = """
        (function() {
            return {
                url: location.href,
                title: document.title,
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
                documentHeight: document.documentElement.scrollHeight,
                visibleElements: document.querySelectorAll('a,button,input,select,textarea,[role="button"]').length,
                forms: document.forms.length,
            };
        })()
        """
        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        return result.get("result", {}).get("value", {})

    # ── Scrolling ──────────────────────────────────────────

    async def scroll_by(self, dx: float = 0, dy: float = 0) -> None:
        """Scroll the page by a given amount."""
        await self._cdp.send(
            "Runtime.evaluate",
            {"expression": f"window.scrollBy({dx}, {dy})"},
        )
        await asyncio.sleep(0.1)
        # Update internal scroll offset
        layout = await self._cdp.send("Page.getLayoutMetrics")
        visual = layout.get("cssVisualViewport", {})
        self._content_offset_x = visual.get("pageX", 0)
        self._content_offset_y = visual.get("pageY", 0)

    async def scroll_to(self, x: float = 0, y: float = 0) -> None:
        """Scroll the page to absolute position."""
        await self._cdp.send(
            "Runtime.evaluate",
            {"expression": f"window.scrollTo({x}, {y})"},
        )
        await asyncio.sleep(0.1)
        self._content_offset_x = x
        self._content_offset_y = y

    async def scroll_into_view(self, selector: str) -> None:
        """Scroll an element into view by selector."""
        await self._cdp.send(
            "Runtime.evaluate",
            {
                "expression": f"""
                (function() {{
                    const el = document.querySelector({_js_string(selector)});
                    if (el) el.scrollIntoView({{behavior: 'instant', block: 'center'}});
                }})()
                """
            },
        )

    # ── JS execution ───────────────────────────────────────

    async def eval(self, expression: str, return_by_value: bool = True) -> Any:
        """Execute arbitrary JavaScript in the page and return the result."""
        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": return_by_value},
        )
        if return_by_value:
            return result.get("result", {}).get("value")
        return result

    # ── Screenshot ─────────────────────────────────────────

    async def screenshot(self, path: str | None = None) -> bytes:
        """Capture a page screenshot via CDP.

        Args:
            path: If given, saves to this file path.
        Returns:
            PNG bytes.
        """
        import base64

        result = await self._cdp.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result["data"])
        if path:
            with open(path, "wb") as f:
                f.write(data)
        return data

    # ── Form interaction helpers ───────────────────────────

    async def get_input_fields(self) -> list[dict]:
        """List all visible input/textarea/select elements."""
        js = """
        (function() {
            const fields = document.querySelectorAll('input:not([type=hidden]),textarea,select');
            return Array.from(fields)
                .filter(el => el.offsetParent !== null)
                .map(el => {
                    const rect = el.getBoundingClientRect();
                    let label = '';
                    if (el.labels && el.labels[0]) label = el.labels[0].textContent.trim();
                    return {
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        label: label,
                        value: el.value || '',
                        x: rect.x, y: rect.y,
                        width: rect.width, height: rect.height,
                    };
                });
        })()
        """
        result = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        fields = result.get("result", {}).get("value", [])
        for f in fields:
            if "x" in f:
                self._compute_screen_coords(f, is_dict=True)
        return fields

    # ── Coordinate maths ───────────────────────────────────

    def _compute_screen_coords(self, el: Element | dict, is_dict: bool = False) -> None:
        """Convert viewport-relative CSS coords to absolute screen coords.

        Accounting for:
        - Window chrome (title bar, tabs, bookmarks bar) — window_offset
        - Scroll offset (content_offset)
        - Device pixel ratio
        - Page zoom
        """
        if is_dict:
            css_x, css_y = el["x"], el["y"]
        else:
            css_x, css_y = el.x, el.y

        # Adjust for scroll position (viewport-relative → page-relative)
        page_x = css_x + self._content_offset_x
        page_y = css_y + self._content_offset_y

        # Apply device pixel ratio (CSS px → device px)
        device_x = page_x * self._device_pixel_ratio * self._page_zoom
        device_y = page_y * self._device_pixel_ratio * self._page_zoom

        # Chrome UI elements that push the content area down/right
        # Approximate: tab bar ~80px, bookmarks ~30px, window border ~8px
        chrome_ui_top = 88  # title bar + tab strip + address bar
        chrome_ui_left = 8  # window border

        screen_x = device_x + self._window_offset_x + chrome_ui_left
        screen_y = device_y + self._window_offset_y + chrome_ui_top

        if is_dict:
            el["screen_x"] = screen_x
            el["screen_y"] = screen_y
            el["center_x"] = screen_x + el["width"] / 2
            el["center_y"] = screen_y + el["height"] / 2
        else:
            el.screen_x = screen_x
            el.screen_y = screen_y

    def _update_scroll_offset(self, x: float, y: float) -> None:
        """Update cached scroll position."""
        self._content_offset_x = x
        self._content_offset_y = y


def _js_string(s: str) -> str:
    """Escape a string for safe embedding in JavaScript."""
    import json
    return json.dumps(s)

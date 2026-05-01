"""
Chimera MCP Server — exposes Chimera's browser control as MCP tools.

Protocol: JSON-RPC 2.0 over stdio (Model Context Protocol)

Usage:
    Configure in ~/.claude/claude_tools.json or claude_desktop_config.json:
    {
        "chimera": {
            "command": "python",
            "args": ["-m", "chimera.mcp_server"]
        }
    }

    Or run directly:
        python -m chimera.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

# ═══════════════════════════════════════════════════════════════
# Lightweight MCP JSON-RPC framework
# ═══════════════════════════════════════════════════════════════


@dataclass
class MCPRequest:
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResponse:
    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"


class MCPServer:
    """Minimal MCP server over stdio."""

    def __init__(self, name: str = "chimera", version: str = "2.0.0"):
        self._name = name
        self._version = version
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, Callable] = {}
        self._initialized = False

    def tool(self, name: str, description: str, input_schema: dict):
        """Decorator to register a tool."""

        def wrapper(fn):
            self._tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": input_schema,
            }
            self._handlers[name] = fn
            return fn

        return wrapper

    def run(self):
        """Main loop: read JSON-RPC from stdin, write responses to stdout.

        Uses synchronous I/O on the transport layer to avoid Windows
        asyncio pipe issues (connect_read_pipe is unreliable on Windows).
        Tool handlers can still be async — they're run via asyncio.run().
        """
        # Use binary stdin to avoid encoding issues on Windows
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer

        buf = b""
        while True:
            chunk = stdin.read(65536)
            if not chunk:
                break

            buf += chunk
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue

                try:
                    request = MCPRequest(**json.loads(line))
                except Exception:
                    continue

                response = self._run_handler(request)
                if response is not None:
                    resp_json = json.dumps(response.__dict__, default=str, ensure_ascii=False)
                    stdout.write((resp_json + "\n").encode("utf-8"))
                    stdout.flush()

    @staticmethod
    def _respond(req_id, result=None, error=None):
        """Build a JSON-RPC response."""
        if req_id is None:
            return None
        err_dict = None
        if error:
            err_dict = {"code": -32000, "message": str(error)}
        return MCPResponse(id=req_id, result=result, error=err_dict)

    def _run_handler(self, req: MCPRequest) -> MCPResponse | None:
        """Run a handler, wrapping async handlers in asyncio.run()."""
        try:
            import asyncio as _asyncio

            result = self._handle_sync(req)
            if _asyncio.iscoroutine(result):
                result = _asyncio.run(result)
            return result
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return self._respond(req.id, error=str(e))

    async def _handle_sync(self, req: MCPRequest) -> MCPResponse | None:
        method = req.method
        params = req.params or {}

        if method == "initialize":
            return self._respond(
                req.id,
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": self._name,
                        "version": self._version,
                    },
                },
            )

        if method == "notifications/initialized":
            self._initialized = True
            return None

        if method == "tools/list":
            return self._respond(
                req.id,
                result={"tools": list(self._tools.values())},
            )

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if tool_name not in self._handlers:
                return self._respond(req.id, error=f"Unknown tool: {tool_name}")

            try:
                result = await self._handlers[tool_name](**arguments)
                return self._respond(req.id, result={"content": [{"type": "text", "text": str(result)}]})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                return self._respond(req.id, error=str(e))

        if method == "ping":
            return self._respond(req.id, result={})

        return self._respond(req.id, error=f"Unknown method: {method}")


# ═══════════════════════════════════════════════════════════════
# Chimera MCP Server
# ═══════════════════════════════════════════════════════════════

_server = MCPServer("chimera", "2.0.0")

# Global browser instance — lazily created and reused across tool calls
_browser_instance: Any = None


async def _get_browser(port: int = 9222) -> Any:
    """Get or create a browser connection. Reuses an existing one if available."""
    global _browser_instance
    from chimera import Chimera

    if _browser_instance is not None:
        try:
            _ = await _browser_instance.title
            return _browser_instance
        except Exception:
            _browser_instance = None

    # Try attaching to an already-running Chrome first
    try:
        _browser_instance = await Chimera.attach(port=port)
        return _browser_instance
    except Exception:
        pass

    # Launch new Chrome with port mode (easier to debug/reconnect)
    _browser_instance = await Chimera.launch(
        "about:blank", debug_mode="port", debug_port=port
    )
    return _browser_instance


# ═══════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════


@_server.tool(
    "chimera_navigate",
    "Navigate the browser to a URL. Launch Chrome if not already running.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to navigate to"},
            "port": {"type": "integer", "description": "Chrome debug port (default 9222)"},
        },
        "required": ["url"],
    },
)
async def chimera_navigate(url: str, port: int = 9222) -> str:
    b = await _get_browser(port)
    await b.goto(url)
    title = await b.title
    return f"Navigated to: {title}\nURL: {url}"


@_server.tool(
    "chimera_click",
    "Click an element by CSS selector or visible text. Uses human-like mouse movement.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector like '#login-btn' or 'button.submit'"},
            "by_text": {"type": "string", "description": "Click by visible text instead of CSS selector"},
        },
    },
)
async def chimera_click(selector: str = "", by_text: str = "") -> str:
    b = await _get_browser()
    if by_text:
        dur = await b.click_text(by_text)
        return f"Clicked text '{by_text}' ({dur:.2f}s)"
    elif selector:
        dur = await b.click(selector)
        return f"Clicked '{selector}' ({dur:.2f}s)"
    return "Error: provide selector or by_text"


@_server.tool(
    "chimera_type",
    "Type text into an input field with human-like rhythm.",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the input field"},
            "text": {"type": "string", "description": "Text to type"},
            "submit": {"type": "boolean", "description": "Press Enter after typing"},
        },
        "required": ["selector", "text"],
    },
)
async def chimera_type(selector: str, text: str, submit: bool = False) -> str:
    b = await _get_browser()
    dur = await b.type_into_field(selector, text, submit=submit)
    action = "typed + Enter" if submit else "typed"
    return f"{action} '{text[:40]}' into '{selector}' ({dur:.2f}s)"


@_server.tool(
    "chimera_read",
    "Read the visible text content of the current page.",
    {
        "type": "object",
        "properties": {
            "max_length": {"type": "integer", "description": "Maximum characters to return (default 5000)"},
        },
    },
)
async def chimera_read(max_length: int = 5000) -> str:
    b = await _get_browser()
    text = await b.text()
    if len(text) > max_length:
        text = text[:max_length] + f"\n... (truncated, {len(text)} total chars)"
    return text


@_server.tool(
    "chimera_screenshot",
    "Take a screenshot of the current page.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to save screenshot (default: screenshot.png)"},
        },
    },
)
async def chimera_screenshot(path: str = "screenshot.png") -> str:
    b = await _get_browser()
    await b.screenshot(path)
    return f"Screenshot saved to {path}"


@_server.tool(
    "chimera_eval",
    "Execute JavaScript in the page and return the result.",
    {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
        },
        "required": ["expression"],
    },
)
async def chimera_eval(expression: str) -> str:
    b = await _get_browser()
    result = await b.eval(expression)
    return json.dumps(result, default=str, ensure_ascii=False)


@_server.tool(
    "chimera_scroll",
    "Scroll the page up or down.",
    {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "description": "up, down, top, or bottom"},
            "amount": {"type": "integer", "description": "Scroll amount in pixels (for up/down)"},
        },
        "required": ["direction"],
    },
)
async def chimera_scroll(direction: str = "down", amount: int = 300) -> str:
    b = await _get_browser()
    if direction == "down":
        await b.scroll_down(amount)
    elif direction == "up":
        await b.scroll_up(amount)
    elif direction == "top":
        await b.scroll_to_top()
    elif direction == "bottom":
        await b.scroll_to_bottom()
    else:
        return f"Unknown direction: {direction}"
    return f"Scrolled {direction}"


@_server.tool(
    "chimera_inputs",
    "List all visible input fields on the page with their labels and types.",
    {
        "type": "object",
        "properties": {},
    },
)
async def chimera_inputs() -> str:
    b = await _get_browser()
    fields = await b.get_input_fields()
    if not fields:
        return "No input fields found on page."
    lines = []
    for f in fields:
        selector = f"{f.get('tag','')}[name='{f.get('name','')}']"
        label = f.get("label", "") or f.get("placeholder", "")
        lines.append(f"  {selector} type={f.get('type','')} — {label}")
    return f"Found {len(fields)} input fields:\n" + "\n".join(lines)


@_server.tool(
    "chimera_page_info",
    "Get current page info: URL, title, scroll position, viewport size.",
    {
        "type": "object",
        "properties": {},
    },
)
async def chimera_page_info() -> str:
    b = await _get_browser()
    state = await b._dom.get_page_state()
    return json.dumps(state, indent=2, ensure_ascii=False)


@_server.tool(
    "chimera_close",
    "Close the browser and end the session.",
    {
        "type": "object",
        "properties": {},
    },
)
async def chimera_close() -> str:
    global _browser_instance
    if _browser_instance:
        await _browser_instance.close()
        _browser_instance = None
    return "Browser closed"


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


def main():
    """Run the Chimera MCP server."""
    _server.run()


if __name__ == "__main__":
    main()

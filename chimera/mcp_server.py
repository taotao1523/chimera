"""
Chimera MCP Server — exposes Chimera's browser control as MCP tools.

Uses the official Python MCP SDK for proper Windows stdio transport.
Registered via: claude mcp add chimera -- python -m chimera.mcp_server
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ═══════════════════════════════════════════════════════════════
# Browser instance — lazily created and reused
# ═══════════════════════════════════════════════════════════════

_browser_instance: Any = None


async def _get_browser(port: int = 9222):
    global _browser_instance
    from chimera import Chimera

    if _browser_instance is not None:
        try:
            _ = await _browser_instance.title
            return _browser_instance
        except Exception:
            _browser_instance = None

    try:
        _browser_instance = await Chimera.attach(port=port)
        return _browser_instance
    except Exception:
        pass

    _browser_instance = await Chimera.launch("about:blank", debug_mode="port", debug_port=port)
    return _browser_instance


# ═══════════════════════════════════════════════════════════════
# Tool definitions
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    Tool(
        name="chimera_navigate",
        description="Navigate the browser to a URL. Launch Chrome if not already running.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "port": {"type": "integer", "description": "Chrome debug port (default 9222)"},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="chimera_click",
        description="Click an element by CSS selector or visible text. Uses human-like mouse movement.",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector like '#login-btn'"},
                "by_text": {"type": "string", "description": "Click by visible text instead of CSS selector"},
            },
        },
    ),
    Tool(
        name="chimera_type",
        description="Type text into an input field with human-like rhythm.",
        inputSchema={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input field"},
                "text": {"type": "string", "description": "Text to type"},
                "submit": {"type": "boolean", "description": "Press Enter after typing"},
            },
            "required": ["selector", "text"],
        },
    ),
    Tool(
        name="chimera_read",
        description="Read the visible text content of the current page.",
        inputSchema={
            "type": "object",
            "properties": {
                "max_length": {"type": "integer", "description": "Max characters (default 5000)"},
            },
        },
    ),
    Tool(
        name="chimera_screenshot",
        description="Take a screenshot of the current page.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (default: screenshot.png)"},
            },
        },
    ),
    Tool(
        name="chimera_eval",
        description="Execute JavaScript in the page and return the result.",
        inputSchema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
            },
            "required": ["expression"],
        },
    ),
    Tool(
        name="chimera_scroll",
        description="Scroll the page up, down, to top, or to bottom.",
        inputSchema={
            "type": "object",
            "properties": {
                "direction": {"type": "string", "description": "up, down, top, or bottom"},
                "amount": {"type": "integer", "description": "Scroll amount in pixels (for up/down)"},
            },
            "required": ["direction"],
        },
    ),
    Tool(
        name="chimera_inputs",
        description="List all visible input fields on the page with their labels and types.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="chimera_page_info",
        description="Get current page info: URL, title, scroll position, viewport size.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="chimera_close",
        description="Close the browser and end the session.",
        inputSchema={"type": "object", "properties": {}},
    ),
]

# ═══════════════════════════════════════════════════════════════
# MCP Server
# ═══════════════════════════════════════════════════════════════

app = Server("chimera")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    b = await _get_browser(arguments.get("port", 9222))

    if name == "chimera_navigate":
        await b.goto(arguments["url"])
        title = await b.title
        return [TextContent(type="text", text=f"Navigated to: {title}\nURL: {arguments['url']}")]

    elif name == "chimera_click":
        if arguments.get("by_text"):
            dur = await b.click_text(arguments["by_text"])
            return [TextContent(type="text", text=f"Clicked text '{arguments['by_text']}' ({dur:.2f}s)")]
        elif arguments.get("selector"):
            dur = await b.click(arguments["selector"])
            return [TextContent(type="text", text=f"Clicked '{arguments['selector']}' ({dur:.2f}s)")]
        return [TextContent(type="text", text="Error: provide selector or by_text")]

    elif name == "chimera_type":
        dur = await b.type_into_field(
            arguments["selector"],
            arguments["text"],
            submit=arguments.get("submit", False),
        )
        return [TextContent(type="text", text=f"Typed into '{arguments['selector']}' ({dur:.2f}s)")]

    elif name == "chimera_read":
        text = await b.text()
        max_len = arguments.get("max_length", 5000)
        if len(text) > max_len:
            text = text[:max_len] + f"\n... (truncated, {len(text)} total chars)"
        return [TextContent(type="text", text=text)]

    elif name == "chimera_screenshot":
        path = arguments.get("path", "screenshot.png")
        await b.screenshot(path)
        return [TextContent(type="text", text=f"Screenshot saved to {path}")]

    elif name == "chimera_eval":
        result = await b.eval(arguments["expression"])
        return [TextContent(type="text", text=json.dumps(result, default=str, ensure_ascii=False))]

    elif name == "chimera_scroll":
        direction = arguments.get("direction", "down")
        amount = arguments.get("amount", 300)
        if direction == "down":
            await b.scroll_down(amount)
        elif direction == "up":
            await b.scroll_up(amount)
        elif direction == "top":
            await b.scroll_to_top()
        elif direction == "bottom":
            await b.scroll_to_bottom()
        return [TextContent(type="text", text=f"Scrolled {direction}")]

    elif name == "chimera_inputs":
        fields = await b.get_input_fields()
        if not fields:
            return [TextContent(type="text", text="No input fields found.")]
        lines = []
        for f in fields:
            selector = f"{f.get('tag','')}[name='{f.get('name','')}']"
            label = f.get("label", "") or f.get("placeholder", "")
            lines.append(f"  {selector} type={f.get('type','')} — {label}")
        return [TextContent(type="text", text=f"Found {len(fields)} fields:\n" + "\n".join(lines))]

    elif name == "chimera_page_info":
        state = await b._dom.get_page_state()
        return [TextContent(type="text", text=json.dumps(state, indent=2, ensure_ascii=False))]

    elif name == "chimera_close":
        global _browser_instance
        if _browser_instance:
            await _browser_instance.close()
            _browser_instance = None
        return [TextContent(type="text", text="Browser closed")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()

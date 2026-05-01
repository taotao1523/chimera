"""
Chimera CLI — command-line interface for undetectable browser control.

Usage:
    chimera launch <url>              Launch and navigate
    chimera attach [--port=9222]      Attach to running Chrome
    chimera click <selector>          Click by CSS selector
    chimera click-text <text>         Click by visible text
    chimera type <selector> <text>    Type into input field
    chimera fill '<json>'             Fill multiple form fields
    chimera read                      Print visible page text
    chimera screenshot [path]         Take a screenshot
    chimera eval <js>                 Execute JavaScript
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="CDP eyes + Win32 hands — undetectable browser control",
    )
    sub = parser.add_subparsers(dest="command")

    # launch
    p = sub.add_parser("launch", help="Launch Chrome and navigate")
    p.add_argument("url", nargs="?", help="URL to open")
    p.add_argument("--chrome", help="Path to Chrome executable")
    p.add_argument("--headless", action="store_true", help="Run headless")
    p.add_argument("--profile", help="User data directory")

    # attach
    p = sub.add_parser("attach", help="Attach to running Chrome")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9222)
    p.add_argument("--page", help="URL pattern to match")

    # click
    p = sub.add_parser("click", help="Click element by CSS selector")
    p.add_argument("selector", help="CSS selector")

    # click-text
    p = sub.add_parser("click-text", help="Click element by visible text")
    p.add_argument("text", help="Visible text to find")
    p.add_argument("--exact", action="store_true", help="Exact match")

    # type
    p = sub.add_parser("type", help="Type into an input field")
    p.add_argument("selector", help="CSS selector")
    p.add_argument("text", help="Text to type")
    p.add_argument("--submit", action="store_true", help="Press Enter after typing")

    # fill
    p = sub.add_parser("fill", help="Fill multiple form fields")
    p.add_argument("fields_json", help='JSON like \'{"selector":"value",...}\'')
    p.add_argument("--submit", help="Submit button selector")

    # read
    sub.add_parser("read", help="Print visible page text")

    # html
    sub.add_parser("html", help="Print full page HTML")

    # screenshot
    p = sub.add_parser("screenshot", help="Take a screenshot")
    p.add_argument("path", nargs="?", default="screenshot.png", help="Output file path")

    # eval
    p = sub.add_parser("eval", help="Execute JavaScript")
    p.add_argument("expression", help="JavaScript expression")

    # scroll
    p = sub.add_parser("scroll", help="Scroll the page")
    p.add_argument("direction", choices=["down", "up", "top", "bottom"], default="down")

    # url / title
    sub.add_parser("url", help="Print current URL")
    sub.add_parser("title", help="Print page title")

    # inputs
    sub.add_parser("inputs", help="List visible input fields")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    asyncio.run(_handle_command(args))


async def _handle_command(args):
    from chimera import Chimera

    # Commands that create a new browser session
    if args.command == "launch":
        async with Chimera.launch(
            args.url,
            chrome_path=args.chrome,
            user_data_dir=args.profile,
            headless=args.headless,
        ) as c:
            if args.url:
                print(f"✓ Navigated to: {await c.title}")
                print(f"  URL: {await c.url}")
            else:
                print("✓ Chrome launched")
            await _interactive_loop(c)

    elif args.command == "attach":
        async with await Chimera.attach(args.host, args.port, args.page) as c:
            print(f"✓ Attached to: {await c.title}")
            await _interactive_loop(c)

    else:
        # Commands that need an existing session
        # For simplicity, we launch a new one with about:blank
        print("Commands like 'click'/'type' require an attached browser.")
        print("Use 'chimera launch <url>' first, or start Chrome with:")
        print('  chrome.exe --remote-debugging-port=9222')
        print("Then use 'chimera attach --port=9222'")
        sys.exit(1)


async def _interactive_loop(c):
    """Simple REPL for interactive browser control."""
    print("\nInteractive mode. Commands: click <sel>, type <sel> <txt>, read, screenshot, quit")
    print("Example: click #login-button")
    print("Example: type input[name='email'] hello@world.com\n")

    while True:
        try:
            line = input("chimera> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break

        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()

        try:
            if cmd == "click" and len(parts) >= 2:
                dur = await c.click(parts[1])
                print(f"  ✓ clicked {parts[1]} ({dur:.2f}s)")

            elif cmd == "click-text" and len(parts) >= 2:
                exact = "--exact" in line
                dur = await c.click_text(parts[1], exact=exact)
                print(f"  ✓ clicked text '{parts[1]}' ({dur:.2f}s)")

            elif cmd == "type" and len(parts) >= 3:
                dur = await c.type_into(parts[1], parts[2])
                print(f"  ✓ typed into {parts[1]} ({dur:.2f}s)")

            elif cmd == "read":
                text = await c.text()
                print(text[:5000])

            elif cmd == "html":
                html = await c.html()
                print(html[:5000])

            elif cmd == "screenshot":
                path = parts[1] if len(parts) > 1 else "screenshot.png"
                await c.screenshot(path)
                print(f"  ✓ saved to {path}")

            elif cmd == "url":
                print(f"  {await c.url}")

            elif cmd == "title":
                print(f"  {await c.title}")

            elif cmd == "inputs":
                fields = await c.get_input_fields()
                for f in fields:
                    print(f"  {f.get('tag')}[name={f.get('name')}] type={f.get('type')} - {f.get('label','')} (placeholder: {f.get('placeholder','')})")

            elif cmd == "fill":
                # Parse JSON from the rest of the line
                json_str = parts[1] if len(parts) > 1 else "{}"
                fields = json.loads(json_str)
                dur = await c.fill_form(fields)
                print(f"  ✓ filled {len(fields)} fields ({dur:.2f}s)")

            elif cmd == "scroll" and len(parts) >= 2:
                direction = parts[1]
                if direction == "down":
                    await c.scroll_down()
                elif direction == "up":
                    await c.scroll_up()
                elif direction == "bottom":
                    await c.scroll_to_bottom()
                elif direction == "top":
                    await c.scroll_to_top()
                print(f"  ✓ scrolled {direction}")

            elif cmd == "eval" and len(parts) >= 2:
                result = await c.eval(parts[1])
                print(f"  {result}")

            elif cmd == "fill-form" and len(parts) >= 2:
                json_str = parts[1] if len(parts) > 1 else "{}"
                fields = json.loads(json_str)
                submit = parts[2] if len(parts) > 2 else None
                dur = await c.fill_form(fields, submit)
                print(f"  ✓ filled {len(fields)} fields ({dur:.2f}s)")

            else:
                print(f"  Unknown command: {cmd}")

        except Exception as e:
            print(f"  ✗ Error: {e}")


if __name__ == "__main__":
    main()

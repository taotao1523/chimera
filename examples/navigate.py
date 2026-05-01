"""
Example: Basic navigation and interaction.

Usage:
    python -m examples.navigate
"""

import asyncio
from chimera import Chimera


async def main():
    async with Chimera.launch("https://www.google.com") as c:
        print(f"Page title: {await c.title}")

        # Find the search box and type into it
        await c.type_into("textarea[name='q'], input[name='q']", "Chimera browser automation")

        # Press Enter to search
        from chimera.hardware.keyboard import press_key
        press_key("enter")

        await asyncio.sleep(2)

        # Take a screenshot
        await c.screenshot("search_results.png")
        print("Screenshot saved to search_results.png")


if __name__ == "__main__":
    asyncio.run(main())

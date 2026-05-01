"""
Example: CDP-level custom interaction — when you need raw CDP access.

This shows how to use Chimera's internal CDP client for operations
not yet exposed through the high-level API.
"""

import asyncio
from chimera import Chimera


async def main():
    async with Chimera.launch("https://httpbin.org") as c:
        # Raw CDP: inspect network requests
        await c._cdp.send("Network.enable")

        # Listen for network events
        responses = []

        def on_response(params):
            url = params.get("response", {}).get("url", "")
            status = params.get("response", {}).get("status", 0)
            if "httpbin" in url:
                responses.append((url, status))

        c._cdp.on_event("Network.responseReceived", on_response)

        # Navigate to trigger requests
        await c.goto("https://httpbin.org/get")

        await asyncio.sleep(1)
        print(f"Captured {len(responses)} network responses:")
        for url, status in responses:
            print(f"  [{status}] {url}")

        # Raw CDP: get performance metrics
        metrics = await c._cdp.send("Performance.getMetrics")
        print(f"\nPerformance metrics: {len(metrics.get('metrics', []))} collected")

        # Raw CDP: take a full-page screenshot
        await c.screenshot("httpbin.png")
        print("Screenshot saved to httpbin.png")


if __name__ == "__main__":
    asyncio.run(main())

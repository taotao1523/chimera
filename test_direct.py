"""Direct CDP test — minimal, no Chimera client overhead."""
import asyncio
import json
import base64
import websockets

PAGE_WS = "ws://127.0.0.1:9222/devtools/page/BB8E1FC8218CF4FBF3F7436B4944D315"


async def cdp_send(ws, method, params=None, msg_id=1):
    payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})
    await ws.send(payload)
    while True:
        raw = await ws.recv()
        resp = json.loads(raw)
        if resp.get("id") == msg_id:
            if "error" in resp:
                raise RuntimeError(f"CDP Error: {resp['error']}")
            return resp.get("result", {})
        # else it's an event, ignore


async def main():
    async with websockets.connect(PAGE_WS) as ws:
        # Enable domains
        await cdp_send(ws, "Page.enable")
        await cdp_send(ws, "Runtime.enable")

        # Get detection results
        js = r"""
        (function() {
            var r = {};
            r.webdriver = navigator.webdriver;
            r.plugins = navigator.plugins.length;
            var names = [];
            for (var i = 0; i < navigator.plugins.length; i++) {
                names.push(navigator.plugins[i].name);
            }
            r.pluginNames = names;
            r.languages = navigator.languages;
            r.platform = navigator.platform;
            r.hwConcurrency = navigator.hardwareConcurrency;
            r.deviceMemory = navigator.deviceMemory;
            r.screenW = screen.width;
            r.screenH = screen.height;
            r.colorDepth = screen.colorDepth;

            var canvas = document.createElement('canvas');
            var gl = canvas.getContext('webgl');
            if (gl) {
                var di = gl.getExtension('WEBGL_debug_renderer_info');
                r.webglVendor = di ? gl.getParameter(di.UNMASKED_VENDOR_WEBGL) : 'N/A';
                r.webglRenderer = di ? gl.getParameter(di.UNMASKED_RENDERER_WEBGL) : 'N/A';
            }

            // Parse table
            var table = {};
            var rows = document.querySelectorAll('table tr');
            rows.forEach(function(row) {
                var cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    table[cells[0].textContent.trim()] = cells[1].textContent.trim();
                }
            });
            r.table = table;
            return r;
        })()
        """

        result = await cdp_send(ws, "Runtime.evaluate", {"expression": js, "returnByValue": True})
        r = result["result"]["value"]

        print("=" * 60)
        print("BOT.SANNYSOFT.COM - DETECTION TEST")
        print("=" * 60)
        print(f"  WebDriver:          {r['webdriver']}")
        print(f"  Plugins:            {r['plugins']}")
        for p in r.get("pluginNames", []):
            print(f"                      - {p}")
        print(f"  Languages:          {r['languages']}")
        print(f"  Platform:           {r['platform']}")
        print(f"  HW Concurrency:     {r['hwConcurrency']}")
        print(f"  Device Memory:      {r['deviceMemory']} GB")
        print(f"  Screen:             {r['screenW']}x{r['screenH']} ({r['colorDepth']}-bit)")
        print(f"  WebGL Vendor:       {r['webglVendor']}")
        print(f"  WebGL Renderer:     {r['webglRenderer']}")

        print(f"\n--- Test Table ---")
        passed = 0
        failed = 0
        table = r.get("table", {})
        for name, result_val in sorted(table.items()):
            rlower = result_val.lower()
            if "pass" in rlower or "present" in rlower:
                marker = "+"
                passed += 1
            else:
                marker = "X"
                failed += 1
            print(f"  [{marker}] {name}: {result_val}")
        print(f"\n  TOTAL: {passed} PASS, {failed} FAIL/ISSUE")

        # Screenshot
        ss = await cdp_send(ws, "Page.captureScreenshot", {"format": "png"})
        path = r"D:\codex\chimera\sannysoft_result.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(ss["data"]))
        print(f"\nScreenshot: {path}")


if __name__ == "__main__":
    asyncio.run(main())

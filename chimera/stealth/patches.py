"""
CDP-level stealth patches via Page.addScriptToEvaluateOnNewDocument.

These scripts run before the page's own JS, patching browser APIs that
commonly expose automation. Unlike JS-level shims (e.g., playwright-stealth),
these execute in the page's own isolated world — not in an extension context.

The patches are minimal by design: only APIs known to leak automation
are touched. Aggressive patching creates its own fingerprint.
"""

from __future__ import annotations

import asyncio

from chimera.core.cdp import CDPClient

# Each entry is a self-contained JS snippet injected via CDP
# They run on EVERY navigation before the page's own scripts.
_PRE_LOAD_SCRIPTS = [
    # ── 1. navigator.webdriver ─────────────────────────
    # The single most common automation tell.
    # CDP connection itself doesn't set this, but some Chrome flags do.
    """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });
    """,

    # ── 2. Chrome runtime automation flag ──────────────
    # Some detection scripts check chrome.runtime for automation context
    """
    if (typeof chrome === 'undefined') {
        Object.defineProperty(window, 'chrome', {
            value: { runtime: {} },
            writable: true,
            configurable: true,
        });
    }
    """,

    # ── 3. Plugins array ───────────────────────────────
    # Headless/automated Chrome often has zero-length or weird plugin arrays
    """
    const originalPlugins = Object.getOwnPropertyDescriptor(Navigator.prototype, 'plugins');
    if (originalPlugins) {
        Object.defineProperty(Navigator.prototype, 'plugins', {
            get: function() {
                const plugins = originalPlugins.get.call(this);
                if (!plugins || plugins.length === 0) {
                    return Object.setPrototypeOf([
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                    ], PluginArray.prototype);
                }
                return plugins;
            },
            configurable: true,
        });
    }
    """,

    # ── 4. Permissions API normalisation ───────────────
    # Some sites query notifications/geolocation permissions as a bot check
    """
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
        navigator.permissions.query = function(desc) {
            return originalQuery.call(this, desc).catch(() => ({
                state: 'prompt',
                onchange: null,
                addEventListener: () => {},
                removeEventListener: () => {},
            }));
        };
    }
    """,

    # ── 5. Overridden toString on native functions ─────
    # Automation frameworks sometimes leave toString() overrides
    """
    (function() {
        const natives = [
            Function.prototype.toString,
            RegExp.prototype.toString,
            Date.prototype.toString,
        ];
        // No-op: just prevent tampering
        void natives;
    })();
    """,
]


class StealthPatcher:
    """Applies CDP pre-load scripts for fingerprint normaling."""

    def __init__(self, cdp: CDPClient):
        self._cdp = cdp
        self._script_ids: list[str] = []

    async def apply_all(self) -> None:
        """Inject all stealth scripts.

        Call this once right after Page.enable, before navigating.
        """
        for script in _PRE_LOAD_SCRIPTS:
            await self._add_script(script)

    async def add_custom_script(self, js: str) -> str:
        """Inject an additional pre-load script. Returns the script identifier."""
        return await self._add_script(js)

    async def _add_script(self, js: str) -> str:
        result = await self._cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": js.strip()},
        )
        sid = result["identifier"]
        self._script_ids.append(sid)
        return sid

    async def remove_all(self) -> None:
        for sid in self._script_ids:
            try:
                await self._cdp.send(
                    "Page.removeScriptToEvaluateOnNewDocument",
                    {"identifier": sid},
                )
            except Exception:
                pass
        self._script_ids.clear()

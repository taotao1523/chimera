"""
Chrome DevTools Protocol client — the "eyes" of Chimera.

Supports two transport modes:
- WebSocket (--remote-debugging-port): standard TCP, easy to inspect
- Pipe (--remote-debugging-pipe): via stdin/stdout, stealthier

Protocol ref: https://chromedevtools.github.io/devtools-protocol/
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CDPMessage:
    id: int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CDPMessage:
        return cls(
            id=data.get("id"),
            method=data.get("method"),
            params=data.get("params"),
            result=data.get("result"),
            error=data.get("error"),
        )

    @property
    def is_event(self) -> bool:
        return self.id is None and self.method is not None

    @property
    def is_response(self) -> bool:
        return self.id is not None


class CDPError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.data = data
        super().__init__(f"CDP Error [{code}]: {message}")


class CDPTransport:
    """Abstract transport for CDP communication."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def send(self, message: bytes) -> None: ...

    async def recv(self) -> bytes: ...


class WebSocketTransport(CDPTransport):
    """CDP over WebSocket (--remote-debugging-port mode)."""

    def __init__(self, ws_url: str):
        self._url = ws_url
        self._ws: Any = None

    async def connect(self) -> None:
        import websockets

        self._ws = await websockets.connect(
            self._url,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**26,
        )

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()

    async def send(self, message: bytes) -> None:
        await self._ws.send(message)

    async def recv(self) -> bytes:
        return await self._ws.recv()


class PipeTransport(CDPTransport):
    """CDP over stdin/stdout pipe (--remote-debugging-pipe mode).

    Messages are framed as: [4-byte big-endian length][JSON payload]
    """

    def __init__(self, process: subprocess.Popen):
        self._process = process
        self._lock = asyncio.Lock()
        self._buf = bytearray()

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    async def send(self, message: bytes) -> None:
        loop = asyncio.get_running_loop()
        length = len(message)
        header = length.to_bytes(4, "big")
        async with self._lock:
            await loop.run_in_executor(None, self._process.stdin.write, header + message)
            await loop.run_in_executor(None, self._process.stdin.flush)

    async def recv(self) -> bytes:
        loop = asyncio.get_running_loop()
        while True:
            # Try to parse a frame from the buffer
            if len(self._buf) >= 4:
                length = int.from_bytes(self._buf[:4], "big")
                if len(self._buf) >= 4 + length:
                    payload = bytes(self._buf[4:4 + length])
                    del self._buf[:4 + length]
                    return payload

            # Read more data
            chunk = await loop.run_in_executor(None, self._process.stdout.read1, 65536)
            if not chunk:
                raise ConnectionError("Pipe closed by Chrome")
            self._buf.extend(chunk)


class CDPClient:
    """Asynchronous CDP client with command/response/event handling.

    Usage:
        async with CDPClient.connect_pipe(chrome_path) as client:
            await client.send("Page.enable")
            await client.send("Page.navigate", {"url": "https://example.com"})
            dom = await client.send("Runtime.evaluate", {"expression": "document.title"})
    """

    def __init__(self, transport: CDPTransport):
        self._transport = transport
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[CDPMessage]] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._recv_task: asyncio.Task | None = None
        self._connected = False

    # ── Factory methods ──────────────────────────────────────

    @classmethod
    async def connect_websocket(cls, ws_url: str) -> CDPClient:
        transport = WebSocketTransport(ws_url)
        client = cls(transport)
        await client._transport.connect()
        client._start_receiver()
        return client

    @classmethod
    async def connect_pipe(cls, chrome_path: str | Path) -> CDPClient:
        """Launch Chrome with --remote-debugging-pipe and connect."""
        proc = subprocess.Popen(
            [
                str(chrome_path),
                "--remote-debugging-pipe",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={Path.home() / '.chimera' / 'chrome-profile'}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        transport = PipeTransport(proc)
        client = cls(transport)
        client._start_receiver()
        await client._wait_browser_ready()
        return client

    @classmethod
    async def connect_port(cls, host: str = "127.0.0.1", port: int = 9222) -> CDPClient:
        """Connect to an already-running Chrome via --remote-debugging-port.

        First fetches /json/version to get the WebSocket URL.
        """
        import urllib.request

        version_url = f"http://{host}:{port}/json/version"
        resp = urllib.request.urlopen(version_url)
        data = json.loads(resp.read())
        ws_url = data["webSocketDebuggerUrl"]
        return await cls.connect_websocket(ws_url)

    @classmethod
    async def connect_port_specific(
        cls, host: str = "127.0.0.1", port: int = 9222, url_pattern: str | None = None
    ) -> CDPClient:
        """Connect to a specific page/target on a running Chrome.

        If url_pattern is given, connects to the first page matching it.
        """
        import urllib.request

        list_url = f"http://{host}:{port}/json"
        resp = urllib.request.urlopen(list_url)
        pages = json.loads(resp.read())

        target = None
        for p in pages:
            if p.get("type") == "page":
                if url_pattern is None or url_pattern in p.get("url", ""):
                    target = p
                    break

        if target is None:
            pages_str = [p.get("url", "?") for p in pages if p.get("type") == "page"]
            raise RuntimeError(
                f"No matching page found. Available: {pages_str}"
            )

        return await cls.connect_websocket(target["webSocketDebuggerUrl"])

    @classmethod
    async def from_process(cls, process: subprocess.Popen) -> CDPClient:
        """Attach CDP to an already-running Chrome process with pipe mode.

        The process must have been started with --remote-debugging-pipe.
        """
        transport = PipeTransport(process)
        client = cls(transport)
        await transport.connect()
        client._start_receiver()
        return client

    # ── Connection lifecycle ─────────────────────────────────

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        await self._transport.close()
        self._connected = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Core send/receive ────────────────────────────────────

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a CDP command and wait for its response."""
        self._msg_id += 1
        msg_id = self._msg_id
        payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})

        future: asyncio.Future[CDPMessage] = asyncio.get_running_loop().create_future()
        if msg_id in self._pending:
            raise RuntimeError(f"Duplicate message id: {msg_id}")
        self._pending[msg_id] = future

        await self._transport.send(payload.encode("utf-8"))

        result = await asyncio.wait_for(future, timeout=30.0)
        if result.error:
            raise CDPError(
                code=result.error.get("code", -1),
                message=result.error.get("message", "unknown"),
                data=result.error.get("data"),
            )
        return result.result or {}

    def on_event(self, method: str, handler: Callable[[dict[str, Any]], Any]):
        """Register a handler for CDP events."""
        self._event_handlers.setdefault(method, []).append(handler)

    def remove_event_handler(self, method: str, handler: Callable):
        handlers = self._event_handlers.get(method, [])
        if handler in handlers:
            handlers.remove(handler)

    # ── Internals ────────────────────────────────────────────

    def _start_receiver(self) -> None:
        self._recv_task = asyncio.get_running_loop().create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            while True:
                raw = await self._transport.recv()
                msg = CDPMessage.from_json(json.loads(raw))

                if msg.is_response:
                    future = self._pending.pop(msg.id, None)
                    if future and not future.done():
                        future.set_result(msg)
                elif msg.is_event:
                    handlers = self._event_handlers.get(msg.method, [])
                    for h in handlers:
                        try:
                            result = h(msg.params or {})
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.exception("Event handler %s failed", msg.method)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CDP receive loop crashed")

    async def _wait_browser_ready(self, timeout: float = 15.0) -> None:
        """For pipe mode: wait until the browser has a page target ready."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                await self.send("Target.getTargets")
                return
            except (CDPError, ConnectionError):
                await asyncio.sleep(0.3)
        raise TimeoutError("Chrome did not become ready in time")

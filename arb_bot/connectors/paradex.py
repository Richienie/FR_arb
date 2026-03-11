from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .base import BaseConnector

# Try to import official SDK; if unavailable (e.g., Python 3.13 incompatibility), fallback to raw WS/REST.
_SDK_AVAILABLE = False
try:
    from paradex_py import Paradex  # type: ignore
    from paradex_py.environment import Environment  # type: ignore
    _SDK_AVAILABLE = True
except ImportError:
    Paradex = None  # type: ignore
    Environment = None  # type: ignore


class ParadexConnector(BaseConnector):
    """
    Paradex connector.

    Protocol requirements (strict):
    - Priority A: Use official Paradex Python SDK (paradex_py) for interaction/auth.
    - Priority B: For market data (funding), use WebSocket funding_data channel.

    Fallback: If SDK is unavailable (Python version mismatch), use raw aiohttp/websockets.

    Key field for market:
      results[].symbol (commonly like "BTC-USD-PERP")
    """

    name = "Paradex"
    REST_BASE = "https://api.prod.paradex.trade/v1"
    WS_URL = "wss://ws.api.prod.paradex.trade/v1"

    def __init__(self, session) -> None:
        super().__init__(session)
        self._client = None

    def _get_client(self):
        """
        Lazily initialize Paradex SDK client.
        Returns None if SDK is not available.
        """
        if not _SDK_AVAILABLE:
            return None

        if self._client is not None:
            return self._client

        env_name = os.getenv("PARADEX_ENV", "PROD").strip().upper()
        env = getattr(Environment, env_name, Environment.PROD)

        l1_address = os.getenv("PARADEX_L1_ADDRESS")
        l1_private_key = os.getenv("PARADEX_L1_PRIVATE_KEY")

        if l1_address and l1_private_key:
            self._client = Paradex(env=env, l1_address=l1_address, l1_private_key=l1_private_key)
        else:
            self._client = Paradex(env=env)

        return self._client

    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch markets.
        Priority A: SDK -> Priority C: REST fallback.
        """
        # Try SDK first
        if _SDK_AVAILABLE:
            try:
                client = self._get_client()
                if client is not None:
                    if hasattr(client, "get_markets"):
                        res = await client.get_markets()
                        return self._coerce_list_of_dicts(res)
                    rest = getattr(client, "rest_client", None)
                    if rest is not None and hasattr(rest, "get_markets"):
                        res = await rest.get_markets()
                        return self._coerce_list_of_dicts(res)
            except Exception:
                pass  # Fallback to REST

        # Fallback: raw aiohttp REST
        return await self._fetch_markets_rest()

    async def _fetch_markets_rest(self) -> List[Dict[str, Any]]:
        """Fallback REST fetch for markets."""
        url = f"{self.REST_BASE}/markets"
        data = await self._get_json(url, timeout_s=10.0)
        return self._coerce_list_of_dicts(data)

    async def stream_funding_data(
        self,
        channel: str = "funding_data.ALL",
        *,
        callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """
        Stream funding data.
        Priority A: SDK WS -> Priority B: Raw websockets fallback.

        - Heartbeat/ping-pong and auto-reconnect are handled.
        - `callback(channel, data)` receives the decoded message.
        """
        # Try SDK first
        if _SDK_AVAILABLE:
            try:
                client = self._get_client()
                if client is not None:
                    ws = getattr(client, "ws_client", None)
                    if ws is not None:
                        async def _on_msg(ch: str, msg: Any) -> None:
                            if callback is not None and isinstance(msg, dict):
                                await callback(ch, msg)

                        await ws.connect()
                        await ws.subscribe(channel, callback=_on_msg)
                        return
            except Exception:
                pass  # Fallback to raw WS

        # Fallback: raw websockets
        await self._stream_funding_raw(channel, callback=callback)

    async def _stream_funding_raw(
        self,
        channel: str,
        *,
        callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """
        Fallback: raw websockets for funding_data.
        Implements heartbeat (pong) and auto-reconnect with exponential backoff.
        """
        import websockets

        attempt = 0
        while True:
            attempt += 1
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    # Subscribe using JSON-RPC
                    sub_msg = json.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": channel},
                        "id": 1,
                    })
                    await ws.send(sub_msg)
                    attempt = 0  # Reset on successful connect

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        # Handle subscription data
                        if msg.get("method") == "subscription":
                            params = msg.get("params", {})
                            ch = params.get("channel", channel)
                            data = params.get("data", {})
                            if callback is not None and isinstance(data, dict):
                                await callback(ch, data)

            except Exception:
                # Exponential backoff with jitter
                delay = min(30.0, 0.5 * (2 ** min(6, attempt))) * (1 + random.random() * 0.3)
                await asyncio.sleep(delay)

    def _coerce_list_of_dicts(self, payload: Any) -> List[Dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "markets"):
                v = payload.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            return []
        return []

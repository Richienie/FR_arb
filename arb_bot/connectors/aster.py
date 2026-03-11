from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Dict, List

import websockets

from .base import BaseConnector


class AsterConnector(BaseConnector):
    """
    Aster (futures-style) connector.

    Endpoint:
      GET https://fapi.asterdex.com/fapi/v1/exchangeInfo

    Key field for market:
      symbols[].symbol (e.g. "BTCUSDT")
    """

    name = "Aster"
    EXCHANGE_INFO_URL = "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
    WS_BASE = "wss://fstream.asterdex.com/ws"

    async def fetch_markets(self) -> List[Dict[str, Any]]:
        data = await self._get_json(self.EXCHANGE_INFO_URL, timeout_s=10.0)
        if not isinstance(data, dict):
            return []

        symbols = data.get("symbols")
        if isinstance(symbols, list):
            return [x for x in symbols if isinstance(x, dict)]

        # some futures APIs may use "data"
        if isinstance(data.get("data"), dict) and isinstance(data["data"].get("symbols"), list):
            return [x for x in data["data"]["symbols"] if isinstance(x, dict)]

        return []

    async def stream_mark_price(
        self,
        symbols: List[str],
        *,
        on_message,
        update_speed: str = "@1s",
    ) -> None:
        """
        Priority B (WSS): Stream mark price + funding rate via Binance-futures style stream:
          <symbol>@markPrice or <symbol>@markPrice@1s

        Heartbeat:
          websockets library automatically responds to ping frames; we also set ping_interval.

        Auto-reconnect:
          reconnect forever with exponential backoff.
        """
        if not symbols:
            return

        # Aster expects lowercase symbols in stream name.
        streams = [f"{s.lower()}@markPrice{update_speed}" for s in symbols]
        # Combined streams endpoint (Binance-compatible)
        url = f"wss://fstream.asterdex.com/stream?streams={'/'.join(streams)}"

        attempt = 0
        while True:
            attempt += 1
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    attempt = 0
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        await on_message(msg)
            except Exception:
                # backoff with jitter
                await asyncio.sleep(min(10.0, 0.5 * (2 ** min(6, attempt))) * (1 + random.random() * 0.2))


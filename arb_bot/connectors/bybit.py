from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional

import websockets

from .base import BaseConnector


class BybitConnector(BaseConnector):
    """
    Bybit connector for perpetual futures funding rates.

    API: https://api.bybit.com
    Docs: https://bybit-exchange.github.io/docs/v5/websocket/public/ticker

    Funding Rate:
    - Payment: Every 8 hours (00:00, 08:00, 16:00 UTC)
    - API returns 8h rate in decimal form
    - WebSocket: tickers.{symbol} topic (includes fundingRate in snapshot/delta)
    """
    
    name = "Bybit"
    REST_BASE = "https://api.bybit.com"
    WS_BASE = "wss://stream.bybit.com/v5/public/linear"
    
    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch all perpetual markets with current funding rates.
        
        Endpoint: GET /v5/market/tickers?category=linear
        Returns list of symbols with fundingRate and fundingIntervalHour.
        """
        url = f"{self.REST_BASE}/v5/market/tickers"
        params = {"category": "linear"}
        data = await self._get_json(url, params=params, timeout_s=15.0)
        
        if not isinstance(data, dict):
            return []
        
        result = data.get("result", {})
        if not isinstance(result, dict):
            return []
        
        list_data = result.get("list", [])
        if isinstance(list_data, list):
            return list_data
        
        return []
    
    async def stream_ticker_funding(
        self,
        symbols: List[str],
        *,
        on_message: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        """
        Stream ticker data via WebSocket (includes fundingRate).

        Topic format: tickers.{symbol}
        Subscribe format: {"op": "subscribe", "args": ["tickers.BTCUSDT"]}

        The tickers topic returns snapshot (full data) and delta (updates).
        fundingRate is included in snapshot messages.
        """
        if not symbols:
            return

        attempt = 0
        while True:
            attempt += 1
            try:
                async with websockets.connect(
                    self.WS_BASE,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    attempt = 0

                    # Subscribe to tickers topics (includes fundingRate)
                    topics = [f"tickers.{s}" for s in symbols]
                    subscribe_msg = {
                        "op": "subscribe",
                        "args": topics,
                    }
                    await ws.send(json.dumps(subscribe_msg))

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        # Handle subscription response
                        if msg.get("op") == "subscribe":
                            # Subscription confirmation
                            continue

                        # Handle data messages from tickers topic
                        if "topic" in msg and msg["topic"].startswith("tickers."):
                            data = msg.get("data")
                            if data:
                                # Only process if fundingRate is present
                                # (snapshot has it, delta may not)
                                if "fundingRate" in data:
                                    await on_message(data)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Exponential backoff with jitter
                delay = min(30.0, 0.5 * (2 ** min(6, attempt))) * (1 + random.random() * 0.2)
                await asyncio.sleep(delay)

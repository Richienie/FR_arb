from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional

import websockets

from .base import BaseConnector


class BinanceConnector(BaseConnector):
    """
    Binance connector for perpetual futures funding rates.
    
    API: https://fapi.binance.com
    Docs: https://developers.binance.com/docs/derivatives/usds-margined-futures
    
    Funding Rate:
    - Payment: Every 8 hours (00:00, 08:00, 16:00 UTC)
    - Some symbols: 4 hours (from fundingInfo)
    - API returns 8h rate (or 4h) in decimal form
    - WebSocket: markPrice stream includes funding rate
    """
    
    name = "Binance"
    REST_BASE = "https://fapi.binance.com"
    WS_BASE = "wss://fstream.binance.com"
    
    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch all perpetual markets with funding interval info.
        
        Endpoint: GET /fapi/v1/fundingInfo
        Returns list of symbols with fundingIntervalHours.
        """
        url = f"{self.REST_BASE}/fapi/v1/fundingInfo"
        data = await self._get_json(url, timeout_s=15.0)
        
        if not isinstance(data, list):
            return []
        
        return data
    
    async def fetch_funding_rates(self) -> List[Dict[str, Any]]:
        """
        Fetch current funding rates for all symbols.
        
        Endpoint: GET /fapi/v1/premiumIndex (no symbol parameter)
        Returns list with lastFundingRate for all symbols.
        """
        url = f"{self.REST_BASE}/fapi/v1/premiumIndex"
        data = await self._get_json(url, timeout_s=15.0)
        
        if isinstance(data, dict):
            # Single symbol response
            return [data]
        elif isinstance(data, list):
            # Multiple symbols response
            return data
        else:
            return []
    
    async def stream_mark_price(
        self,
        symbols: List[str],
        *,
        on_message: Callable[[Dict[str, Any]], Awaitable[None]],
        update_speed: str = "@1s",
    ) -> None:
        """
        Stream mark price and funding rate via WebSocket.
        
        Stream format: {symbol}@markPrice{update_speed}
        Update speed: "" (3000ms) or "@1s" (1000ms)
        
        Response includes:
        - "r": funding rate (8h rate, decimal)
        - "T": next funding time (ms)
        """
        if not symbols:
            return
        
        # Binance requires lowercase symbols
        streams = [f"{s.lower()}@markPrice{update_speed}" for s in symbols]

        # Safety: Binance combined streams supports up to 1024 streams per connection.
        if len(streams) > 1024:
            raise ValueError(f"Too many Binance streams for one connection: {len(streams)} > 1024")
        
        # Combined streams endpoint
        if len(streams) == 1:
            url = f"{self.WS_BASE}/ws/{streams[0]}"
        else:
            # Multiple streams: use /stream endpoint
            url = f"{self.WS_BASE}/stream?streams={'/'.join(streams)}"
        
        attempt = 0
        while True:
            attempt += 1
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    attempt = 0  # Reset on successful connect
                    
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        
                        # Handle combined stream format
                        if "stream" in msg and "data" in msg:
                            # Combined stream: {"stream": "btcusdt@markPrice", "data": {...}}
                            data = msg["data"]
                        else:
                            # Single stream: direct message
                            data = msg
                        
                        await on_message(data)
                        
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Exponential backoff with jitter
                delay = min(30.0, 0.5 * (2 ** min(6, attempt))) * (1 + random.random() * 0.2)
                await asyncio.sleep(delay)

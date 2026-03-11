from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import aiohttp


class BaseConnector(ABC):
    """
    Abstract base connector for DEX market data.

    Each connector must implement `fetch_markets()` and return a list of raw market objects.
    """

    name: str

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    @abstractmethod
    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch all available markets from the DEX.

        Returns:
            List of raw market objects (dict-like) as returned by the upstream API.
        """
        raise NotImplementedError

    async def _get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: float = 10.0,
    ) -> Optional[Any]:
        """
        Best-effort JSON fetcher.
        On timeout/network error, returns None.
        """
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_s)
            async with self.session.get(url, params=params, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def _sleep_backoff(self, attempt: int, *, base: float = 0.5, cap: float = 10.0) -> None:
        """
        Simple exponential backoff helper.
        """
        delay = min(cap, base * (2 ** max(0, attempt - 1)))
        await asyncio.sleep(delay)


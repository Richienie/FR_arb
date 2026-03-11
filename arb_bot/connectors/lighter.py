from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseConnector

# Try to import official SDK; fallback to raw REST if unavailable.
_SDK_AVAILABLE = False
try:
    import lighter  # type: ignore
    _SDK_AVAILABLE = True
except ImportError:
    lighter = None  # type: ignore


class LighterConnector(BaseConnector):
    """
    Lighter connector.

    API Endpoints (based on official docs):
    - Markets list: https://explorer.elliot.ai/api/markets (Explorer API)
    - Funding rates: https://mainnet.zklighter.elliot.ai/api/v1/funding-rates
    - Order books: https://mainnet.zklighter.elliot.ai/api/v1/orderBooks

    SDK Structure:
    - lighter.ApiClient() - core client
    - lighter.RootApi() - info, status
    - lighter.AccountApi() - account methods
    - lighter.BlockApi() - block methods
    - NO PublicApi class exists!

    Protocol requirements:
    - Priority A: Use Lighter official Python SDK (`lighter-sdk`) if available.
    - Priority C: Fallback to raw aiohttp REST.
    """

    name = "Lighter"

    # Correct endpoints based on research
    EXPLORER_BASE = "https://explorer.elliot.ai"
    MAINNET_BASE = "https://mainnet.zklighter.elliot.ai"

    MARKETS_URL = f"{EXPLORER_BASE}/api/markets"  # Markets list is on Explorer API
    FUNDING_RATES_URL = f"{MAINNET_BASE}/api/v1/funding-rates"
    ORDER_BOOKS_URL = f"{MAINNET_BASE}/api/v1/orderBooks"

    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch markets list from Explorer API.
        Priority A: SDK (RootApi.info) -> Priority C: REST fallback.
        """
        # Try SDK first (use RootApi, not PublicApi which doesn't exist)
        if _SDK_AVAILABLE and lighter is not None:
            try:
                client = lighter.ApiClient()
                try:
                    # RootApi provides basic exchange info
                    root_api = lighter.RootApi(client)
                    info = await root_api.info()
                    # Try to extract markets from info response
                    markets = self._extract_markets_from_info(info)
                    if markets:
                        return markets
                finally:
                    await client.close()
            except Exception:
                pass  # Fallback to REST

        # Fallback: raw aiohttp REST to Explorer API
        return await self._fetch_markets_rest()

    async def _fetch_markets_rest(self) -> List[Dict[str, Any]]:
        """
        Fetch markets from Explorer API.
        Response format: list of {"symbol": "BTC", "market_index": 0}
        """
        data = await self._get_json(self.MARKETS_URL, timeout_s=15.0)
        # Explorer API returns a direct list
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return self._coerce_list_of_dicts(data)

    async def fetch_funding_rates(self) -> List[Dict[str, Any]]:
        """
        Fetch funding rates via REST.
        Endpoint: /api/v1/funding-rates on mainnet.
        Response format: {"code": 0, "funding_rates": [...]}
        """
        data = await self._get_json(self.FUNDING_RATES_URL, timeout_s=15.0)
        if isinstance(data, dict) and "funding_rates" in data:
            return self._coerce_list_of_dicts(data["funding_rates"])
        return self._coerce_list_of_dicts(data)

    async def fetch_order_books_metadata(self) -> List[Dict[str, Any]]:
        """
        Fetch order book metadata (includes market info, fees, min amounts).
        Endpoint: /api/v1/orderBooks on mainnet.
        Response format: {"code": 0, "order_books": [...]}
        """
        data = await self._get_json(self.ORDER_BOOKS_URL, timeout_s=15.0)
        if isinstance(data, dict) and "order_books" in data:
            return self._coerce_list_of_dicts(data["order_books"])
        return self._coerce_list_of_dicts(data)

    def _extract_markets_from_info(self, info: Any) -> List[Dict[str, Any]]:
        """Extract markets from SDK info response."""
        if info is None:
            return []

        # Try different possible structures
        if hasattr(info, "markets"):
            return self._coerce_list_of_dicts(info.markets)
        if hasattr(info, "order_books"):
            return self._coerce_list_of_dicts(info.order_books)
        if isinstance(info, dict):
            for key in ("markets", "order_books", "orderBooks", "data"):
                if key in info:
                    return self._coerce_list_of_dicts(info[key])

        return []

    def _coerce_list_of_dicts(self, payload: Any) -> List[Dict[str, Any]]:
        """Convert various response formats to list of dicts."""
        if payload is None:
            return []

        # Already a list
        if isinstance(payload, list):
            result = []
            for x in payload:
                if isinstance(x, dict):
                    result.append(x)
                elif hasattr(x, "model_dump"):  # Pydantic v2
                    result.append(x.model_dump())
                elif hasattr(x, "dict"):  # Pydantic v1
                    result.append(x.dict())
                elif hasattr(x, "__dict__"):
                    result.append(vars(x))
            return result

        # Dict with nested data
        if isinstance(payload, dict):
            for key in ("data", "results", "markets", "fundingRates", "orderBooks"):
                v = payload.get(key)
                if isinstance(v, list):
                    return self._coerce_list_of_dicts(v)
            # Maybe the dict itself is the data (single market case)
            return []

        # Pydantic model or similar
        if hasattr(payload, "model_dump"):
            return self._coerce_list_of_dicts(payload.model_dump())
        if hasattr(payload, "dict"):
            return self._coerce_list_of_dicts(payload.dict())

        # Iterable fallback
        if hasattr(payload, "__iter__"):
            try:
                return self._coerce_list_of_dicts(list(payload))
            except Exception:
                pass

        return []

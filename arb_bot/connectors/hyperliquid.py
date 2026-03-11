from __future__ import annotations

from typing import Any, Dict, List

from .base import BaseConnector


class HyperliquidConnector(BaseConnector):
    """
    Hyperliquid connector.
    
    API: https://api.hyperliquid.xyz
    Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
    
    Funding Rate:
    - Calculated per 8h, paid hourly (1/8 of 8h rate)
    - API returns 1h rate directly in 'funding' field
    - Format: decimal (0.0001 = 0.01%)
    """
    
    name = "Hyperliquid"
    API_URL = "https://api.hyperliquid.xyz/info"
    
    async def fetch_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch all perpetual markets.
        
        Endpoint: POST /info with type="metaAndAssetCtxs"
        Returns list of assets with funding rates, mark prices, etc.
        """
        payload = {"type": "metaAndAssetCtxs"}
        data = await self._post_json(self.API_URL, payload)
        
        if not isinstance(data, list) or len(data) < 2:
            return []
        
        # Response format: [meta, assetCtxs]
        # meta: universe info
        # assetCtxs: per-asset context (funding, prices, etc.)
        asset_ctxs = data[1] if len(data) > 1 else []
        
        if not isinstance(asset_ctxs, list):
            return []
        
        # Extract symbol from meta
        meta = data[0]
        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        
        # Build markets list
        markets = []
        for i, ctx in enumerate(asset_ctxs):
            if not isinstance(ctx, dict):
                continue
            
            # Get symbol from universe by index
            symbol = universe[i].get("name") if i < len(universe) else f"ASSET_{i}"
            
            # Combine symbol with context
            market = {
                "symbol": symbol,
                "funding": ctx.get("funding"),
                "markPx": ctx.get("markPx"),
                "premium": ctx.get("premium"),
                "openInterest": ctx.get("openInterest"),
            }
            markets.append(market)
        
        return markets
    
    async def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        timeout_s: float = 10.0,
    ) -> Any:
        """POST request with JSON payload."""
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=timeout_s)
            async with self.session.post(url, json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception:
            return None

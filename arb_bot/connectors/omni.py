from __future__ import annotations

from typing import Any, Dict, List

from .base import BaseConnector


class OmniConnector(BaseConnector):
    """
    Omni (Variational) connector.

    Endpoint:
      GET https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats

    Key field for asset:
      listings[].ticker  (e.g. "BTC")
    """

    name = "Omni"
    STATS_URL = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"

    async def fetch_markets(self) -> List[Dict[str, Any]]:
        data = await self._get_json(self.STATS_URL, timeout_s=10.0)
        if not isinstance(data, dict):
            return []

        listings = data.get("listings")
        if isinstance(listings, list):
            out: List[Dict[str, Any]] = []
            for item in listings:
                if isinstance(item, dict):
                    out.append(item)
            return out

        return []


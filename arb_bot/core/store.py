"""
Thread-safe Market Data Store for funding rates.

FINAL TRUTH - DEFAULT_INTERVALS (Based on Official Docs Research):
===================================================================
- Lighter: 1.0h (API returns 8h aggregate, divide by 8 for 1h rate)
- Paradex: 8.0h (API returns 8-hour basis, must divide by 8)
- Aster: 8.0h (Binance-style lastFundingRate)
- Omni: 8.0h (default fallback, actual from funding_interval_s)

Sources:
- Lighter: docs.lighter.xyz - API returns 8h value, website shows 1h rate (paid hourly)
- Paradex: docs.paradex.trade - "funding_rate is per 8 hours"
- Aster: docs.asterdex.com - "Binance-style 8h rate"
===================================================================
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RateEntry:
    """Single funding rate entry for a DEX."""
    rate_1h: float  # Normalized to 1-hour rate (for comparison)
    raw_rate: float  # Original rate value from API
    interval_hours: float  # Original interval in hours
    timestamp: float  # Unix timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rate_1h": self.rate_1h,
            "raw_rate": self.raw_rate,
            "interval_hours": self.interval_hours,
            "ts": self.timestamp,
        }
    
    def get_display_rate(self) -> float:
        """Get rate for display (matching official website)."""
        # Return raw_rate (original interval rate) for display
        return self.raw_rate
    
    def get_interval_label(self) -> str:
        """Get interval label for display."""
        h = self.interval_hours
        if h == 1.0:
            return "1h"
        elif h == 4.0:
            return "4h"
        elif h == 8.0:
            return "8h"
        else:
            return f"{h:.1f}h"


class MarketDataStore:
    """
    Thread-safe store for funding rates across multiple DEXs.
    
    Rate Normalization: rate_1h = raw_rate / interval_hours
    """
    
    # BASED ON OFFICIAL DOCUMENTATION
    # - Lighter: docs.lighter.xyz - API returns 8h aggregate, divide by 8 for 1h rate (paid hourly)
    # - Paradex: docs.paradex.trade - DYNAMIC per symbol (1h/2h/4h/8h), fetched from funding_period_hours
    # - Aster: docs.asterdex.com - default 8h, some symbols 4h/1h (dynamic)
    # - Hyperliquid: hyperliquid.gitbook.io - API returns 1h rate (paid hourly)
    # - Binance: developers.binance.com - DYNAMIC per symbol (8h/4h), fetched from fundingInfo
    # - Bybit: bybit-exchange.github.io - 8h rate (from fundingIntervalHour: "8")
    DEFAULT_INTERVALS = {
        "Lighter": 1.0,   # Hourly payment (API returns 8h aggregate, divide by 8)
        "Paradex": 8.0,   # Default fallback, actual is DYNAMIC per symbol
        "Aster": 8.0,     # Default, actual is DYNAMIC per symbol
        "Omni": 8.0,      # Default fallback (actual from funding_interval_s)
        "Hyperliquid": 1.0,  # Hourly payment
        "Binance": 8.0,   # Default fallback, actual is DYNAMIC per symbol (8h/4h)
        "Bybit": 8.0,    # 8h rate (from fundingIntervalHour)
    }
    
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, RateEntry]] = {}
        self._last_update: Dict[str, float] = {}
    
    def update_rate(
        self,
        dex_name: str,
        symbol: str,
        raw_rate: float,
        interval_hours: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Update funding rate for a symbol on a DEX.
        
        Args:
            dex_name: DEX name (e.g., "Paradex", "Lighter")
            symbol: Normalized symbol (e.g., "BTC", "ETH")
            raw_rate: Raw funding rate from API (decimal form)
            interval_hours: Funding interval in hours. If None, uses default.
            timestamp: Unix timestamp. If None, uses current time.
        """
        if not symbol or raw_rate is None:
            return
        
        if interval_hours is None:
            interval_hours = self.DEFAULT_INTERVALS.get(dex_name, 8.0)
        
        # Safety: prevent divide by zero
        if interval_hours <= 0:
            interval_hours = 8.0
        
        rate_1h = raw_rate / interval_hours
        ts = timestamp if timestamp is not None else time.time()
        
        entry = RateEntry(
            rate_1h=rate_1h,
            raw_rate=raw_rate,
            interval_hours=interval_hours,
            timestamp=ts,
        )
        
        with self._lock:
            if symbol not in self._data:
                self._data[symbol] = {}
            self._data[symbol][dex_name] = entry
            self._last_update[dex_name] = ts
    
    def update_rate_from_apr(
        self,
        dex_name: str,
        symbol: str,
        apr: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """Update funding rate from APR (annual percentage rate)."""
        if apr is None:
            return
        interval_hours = 24.0 * 365.0
        self.update_rate(dex_name, symbol, apr, interval_hours, timestamp)
    
    def get_rate(self, symbol: str, dex_name: str) -> Optional[RateEntry]:
        """Get rate entry for a specific symbol and DEX."""
        with self._lock:
            return self._data.get(symbol, {}).get(dex_name)
    
    def get_all_rates(self, symbol: str) -> Dict[str, RateEntry]:
        """Get all DEX rates for a symbol."""
        with self._lock:
            return dict(self._data.get(symbol, {}))
    
    def get_symbols(self) -> List[str]:
        """Get all symbols in the store."""
        with self._lock:
            return list(self._data.keys())
    
    def get_symbol_coverage(self, symbol: str) -> List[str]:
        """Get list of DEXs that have data for this symbol."""
        with self._lock:
            return list(self._data.get(symbol, {}).keys())
    
    def register_dex(self, dex_name: str) -> None:
        """Register a DEX/CEX name for health checks even before first tick.

        This prevents silent failures where a connector never writes data and therefore
        never appears in stale/last_update monitoring.
        """
        with self._lock:
            # 0.0 means "never updated" and will be considered stale by get_stale_threshold.
            self._last_update.setdefault(dex_name, 0.0)

    def get_last_update(self, dex_name: str) -> Optional[float]:
        """Get last update timestamp for a DEX."""
        with self._lock:
            return self._last_update.get(dex_name)
    
    def get_stale_threshold(self, max_age_seconds: float = 120.0) -> Dict[str, bool]:
        """Check which DEXs have stale data."""
        now = time.time()
        with self._lock:
            return {
                dex: (now - ts) > max_age_seconds
                for dex, ts in self._last_update.items()
            }
    
    def find_spread(self, symbol: str) -> Optional[Tuple[str, str, float, float, float]]:
        """
        Find the max spread for a symbol.
        
        Returns:
            Tuple of (long_dex, short_dex, spread_1h, min_rate, max_rate) or None
        """
        with self._lock:
            dex_data = self._data.get(symbol, {})
            if len(dex_data) < 2:
                return None
            
            min_dex, min_entry = min(dex_data.items(), key=lambda x: x[1].rate_1h)
            max_dex, max_entry = max(dex_data.items(), key=lambda x: x[1].rate_1h)
            
            if min_dex == max_dex:
                return None
            
            spread_1h = max_entry.rate_1h - min_entry.rate_1h
            return (min_dex, max_dex, spread_1h, min_entry.rate_1h, max_entry.rate_1h)
    
    def snapshot(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Get a snapshot of all data."""
        with self._lock:
            return {
                symbol: {dex: entry.to_dict() for dex, entry in dex_data.items()}
                for symbol, dex_data in self._data.items()
            }
    
    def stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        with self._lock:
            total_symbols = len(self._data)
            dex_counts: Dict[str, int] = {}
            for dex_data in self._data.values():
                for dex in dex_data.keys():
                    dex_counts[dex] = dex_counts.get(dex, 0) + 1
            return {
                "total_symbols": total_symbols,
                "dex_symbol_counts": dex_counts,
                "last_updates": dict(self._last_update),
            }

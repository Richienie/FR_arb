"""
Arbitrage Scanner Engine - Hybrid Mode (WebSocket + REST Polling).

BASED ON OFFICIAL DOCUMENTATION:
============================================================
- Lighter: 1h rate (docs.lighter.xyz: "fundingRate = (premium/8) + interestRate", paid hourly)
           -> API returns 8h aggregate, must divide by 8 to get 1h rate for display
- Paradex: 8h rate (docs.paradex.trade: "funding_premium is 8h amount")
           -> interval_hours = 8.0
- Omni: Dynamic (from funding_interval_s), rate in PERCENTAGE form
- Aster: DYNAMIC per symbol (docs.asterdex.com: default 8h, some 4h/1h)
           -> fetch from /fapi/v1/fundingRate history
============================================================
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import aiohttp

from arb_bot.connectors import AsterConnector, BinanceConnector, BybitConnector, HyperliquidConnector, LighterConnector, OmniConnector, ParadexConnector
from arb_bot.core.normalizer import normalize_symbol
from arb_bot.core.store import MarketDataStore

logger = logging.getLogger(__name__)

# Debug whitelist to inspect raw rates
DEBUG_SYMBOLS = {"BTC", "ETH", "SOL", "BERA", "RESOLV"}


@dataclass
class ArbitrageOpportunity:
    symbol: str
    long_dex: str
    short_dex: str
    spread_1h: float
    cashflow_10k_1h: float
    apr: float
    long_rate_1h: float
    short_rate_1h: float
    # NEW: Display rates (original interval rates matching official websites)
    long_rate_display: float
    short_rate_display: float
    long_interval: str
    short_interval: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "long_dex": self.long_dex,
            "short_dex": self.short_dex,
            "spread_1h": self.spread_1h,
            "cashflow_10k_1h": self.cashflow_10k_1h,
            "apr": self.apr,
            "long_rate_1h": self.long_rate_1h,
            "short_rate_1h": self.short_rate_1h,
            "long_rate_display": self.long_rate_display,
            "short_rate_display": self.short_rate_display,
            "long_interval": self.long_interval,
            "short_interval": self.short_interval,
        }


class ArbitrageScanner:
    """
    Hybrid arbitrage scanner combining WebSocket streams and REST polling.
    
    Data Sources (Verified 2026-01-28):
    - Paradex: DYNAMIC per symbol (1h/2h/4h/8h, fetched from funding_period_hours)
    - Aster: DYNAMIC interval per symbol (1h/4h/8h, fetched from history)
    - Omni: Dynamic interval (from funding_interval_s), rate in % form
    - Lighter: API returns 8h aggregate, divide by 8 for 1h rate (paid hourly)
    """
    
    def __init__(
        self,
        common_assets: Set[str],
        store: Optional[MarketDataStore] = None,
        poll_interval_s: float = 60.0,
    ) -> None:
        self.common_assets = common_assets
        self.store = store or MarketDataStore()
        self.poll_interval_s = poll_interval_s
        self._session: Optional[aiohttp.ClientSession] = None
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._paradex: Optional[ParadexConnector] = None
        self._aster: Optional[AsterConnector] = None
        self._omni: Optional[OmniConnector] = None
        self._lighter: Optional[LighterConnector] = None
        self._hyperliquid: Optional[HyperliquidConnector] = None
        self._binance: Optional[BinanceConnector] = None
        self._bybit: Optional[BybitConnector] = None
        self._symbol_maps: Dict[str, Dict[str, str]] = {}
        # Aster: dynamic interval cache {symbol: interval_hours}
        self._aster_intervals: Dict[str, float] = {}
        # Paradex: dynamic funding period cache {symbol: period_hours}
        self._paradex_periods: Dict[str, float] = {}
        # Binance: dynamic funding interval cache {symbol: interval_hours}
        self._binance_intervals: Dict[str, float] = {}
        # Bybit: dynamic funding interval cache {symbol: interval_hours}
        self._bybit_intervals: Dict[str, float] = {}
    
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30.0))
        self._paradex = ParadexConnector(self._session)
        self._aster = AsterConnector(self._session)
        self._omni = OmniConnector(self._session)
        self._lighter = LighterConnector(self._session)
        self._hyperliquid = HyperliquidConnector(self._session)
        self._binance = BinanceConnector(self._session)
        self._bybit = BybitConnector(self._session)
        
        await self._build_symbol_maps()
        await self._build_aster_intervals()  # NEW: fetch dynamic intervals for Aster
        await self._build_paradex_periods()  # NEW: fetch dynamic funding periods for Paradex
        await self._build_binance_intervals()  # NEW: fetch dynamic intervals for Binance
        await self._build_bybit_intervals()  # NEW: fetch dynamic intervals for Bybit
        
        self._tasks.append(asyncio.create_task(self._run_paradex_stream(), name="paradex_stream"))
        self._tasks.append(asyncio.create_task(self._run_aster_stream(), name="aster_stream"))
        self._tasks.append(asyncio.create_task(self._run_omni_poller(), name="omni_poller"))
        self._tasks.append(asyncio.create_task(self._run_lighter_poller(), name="lighter_poller"))
        self._tasks.append(asyncio.create_task(self._run_hyperliquid_poller(), name="hyperliquid_poller"))
        self._tasks.append(asyncio.create_task(self._run_binance_stream(), name="binance_stream"))
        self._tasks.append(asyncio.create_task(self._run_bybit_stream(), name="bybit_stream"))

        # Periodic interval refresh tasks (to catch dynamic interval changes)
        self._tasks.append(asyncio.create_task(self._run_interval_refresher(), name="interval_refresher"))

        logger.info("ArbitrageScanner started with %d common assets", len(self.common_assets))
    
    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("ArbitrageScanner stopped")
    
    async def _build_symbol_maps(self) -> None:
        logger.info("Building symbol maps...")
        tasks = [
            self._paradex.fetch_markets(),
            self._aster.fetch_markets(),
            self._omni.fetch_markets(),
            self._lighter.fetch_markets(),
            self._hyperliquid.fetch_markets(),
            self._binance.fetch_markets(),
            self._bybit.fetch_markets(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # IMPORTANT: Index order must match tasks order above
        # [0] Paradex, [1] Aster, [2] Omni, [3] Lighter, [4] Hyperliquid, [5] Binance, [6] Bybit
        dex_configs = [
            ("Paradex", results[0], "symbol"),
            ("Aster", results[1], "symbol"),
            ("Omni", results[2], "ticker"),
            ("Lighter", results[3], "symbol"),
            ("Hyperliquid", results[4], "symbol"),
            ("Binance", results[5], "symbol"),  # Index 5 = Binance
            ("Bybit", results[6], "symbol"),    # Index 6 = Bybit
        ]
        
        for dex_name, markets, key in dex_configs:
            if isinstance(markets, Exception):
                logger.warning("Failed to fetch %s markets: %s", dex_name, markets)
                continue
            symbol_map: Dict[str, str] = {}
            for m in markets:
                raw = m.get(key, "")
                if not raw:
                    continue
                normalized = normalize_symbol(raw)
                if normalized:
                    # Store all symbols (no filtering) - user requested raw data
                    symbol_map[raw] = normalized
                    symbol_map[raw.lower()] = normalized
            self._symbol_maps[dex_name] = symbol_map
            logger.info("%s: mapped %d symbols", dex_name, len(symbol_map))

    async def _build_aster_intervals(self) -> None:
        """
        Fetch dynamic funding intervals for Aster symbols.
        
        Aster has variable intervals per symbol (1h/4h/8h).
        We derive this by looking at the time difference between
        recent funding rate timestamps.
        """
        logger.info("Building Aster interval map...")
        aster_symbols = [s for s in self._symbol_maps.get("Aster", {}).keys() if s.isupper()]
        
        # Priority symbols first (debug + common), then others
        priority_symbols = []
        other_symbols = []
        for s in aster_symbols:
            normalized = normalize_symbol(s)
            if normalized in DEBUG_SYMBOLS or normalized in self.common_assets:
                priority_symbols.append(s)
            else:
                other_symbols.append(s)
        
        # Sample: all priority + first 30 others
        sample_symbols = priority_symbols + other_symbols[:30]
        logger.info("Fetching intervals for %d Aster symbols...", len(sample_symbols))
        
        for raw_symbol in sample_symbols:
            try:
                url = f"https://fapi.asterdex.com/fapi/v1/fundingRate?symbol={raw_symbol}&limit=2"
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if len(data) >= 2:
                        t1 = int(data[0].get("fundingTime", 0))
                        t2 = int(data[1].get("fundingTime", 0))
                        diff_hours = abs(t1 - t2) / 1000 / 3600
                        if diff_hours > 0:
                            # Round to nearest standard interval
                            if diff_hours < 2:
                                diff_hours = 1.0
                            elif diff_hours < 6:
                                diff_hours = 4.0
                            else:
                                diff_hours = 8.0
                            self._aster_intervals[raw_symbol] = diff_hours
                            normalized = normalize_symbol(raw_symbol)
                            if normalized in DEBUG_SYMBOLS:
                                logger.info("[Aster] %s interval = %.1fh", raw_symbol, diff_hours)
            except Exception:
                pass
            await asyncio.sleep(0.02)  # Rate limit
        
        # Count intervals
        interval_counts: Dict[float, int] = {}
        for intv in self._aster_intervals.values():
            interval_counts[intv] = interval_counts.get(intv, 0) + 1
        logger.info("Aster intervals: %s (fetched %d)", dict(sorted(interval_counts.items())), len(self._aster_intervals))

    def _get_aster_interval(self, raw_symbol: str) -> float:
        """Get interval for Aster symbol, default to 8.0 if unknown."""
        return self._aster_intervals.get(raw_symbol, 8.0)

    async def _build_paradex_periods(self) -> None:
        """
        Build cache of funding periods for Paradex symbols.
        Different assets have different funding_period_hours (1h, 2h, 4h, 8h).
        """
        logger.info("Building Paradex funding periods...")
        try:
            markets = await self._paradex.fetch_markets()
            for m in markets:
                symbol = m.get("symbol", "")
                period = m.get("funding_period_hours")
                if symbol and period is not None:
                    try:
                        self._paradex_periods[symbol] = float(period)
                    except (ValueError, TypeError):
                        pass
            
            # Count periods
            period_counts: Dict[float, int] = {}
            for period in self._paradex_periods.values():
                period_counts[period] = period_counts.get(period, 0) + 1
            logger.info("Paradex periods: %s (fetched %d)", dict(sorted(period_counts.items())), len(self._paradex_periods))
        except Exception as e:
            logger.warning("Failed to build Paradex periods: %s", e)

    def _get_paradex_period(self, raw_symbol: str) -> float:
        """Get funding period for Paradex symbol, default to 8.0 if unknown."""
        return self._paradex_periods.get(raw_symbol, 8.0)

    async def _build_binance_intervals(self) -> None:
        """
        Build cache of funding intervals for Binance symbols.

        IMPORTANT: Binance has dynamic funding intervals (1h/4h/8h) that can change.
        This method fetches current intervals from /fapi/v1/fundingInfo API.
        """
        logger.info("Building Binance funding intervals...")
        try:
            markets = await self._binance.fetch_markets()
            for m in markets:
                symbol = m.get("symbol", "")
                interval = m.get("fundingIntervalHours")
                if symbol and interval is not None:
                    try:
                        self._binance_intervals[symbol] = float(interval)
                    except (ValueError, TypeError):
                        pass

            # Count intervals
            interval_counts: Dict[float, int] = {}
            for interval in self._binance_intervals.values():
                interval_counts[interval] = interval_counts.get(interval, 0) + 1
            logger.info("Binance intervals: %s (fetched %d)", dict(sorted(interval_counts.items())), len(self._binance_intervals))
        except Exception as e:
            logger.warning("Failed to build Binance intervals: %s", e)

    def _get_binance_interval(self, raw_symbol: str) -> float:
        """Get funding interval for Binance symbol, default to 8.0 if unknown."""
        return self._binance_intervals.get(raw_symbol, 8.0)

    async def _build_bybit_intervals(self) -> None:
        """
        Build cache of funding intervals for Bybit symbols.
        Different assets have different fundingIntervalHour (1h, 2h, 4h, 8h).
        """
        logger.info("Building Bybit funding intervals...")
        try:
            markets = await self._bybit.fetch_markets()
            for m in markets:
                symbol = m.get("symbol", "")
                # Bybit returns fundingIntervalHour as string (e.g., "4", "8")
                interval_str = m.get("fundingIntervalHour")
                if symbol and interval_str is not None:
                    try:
                        interval = float(interval_str)
                        if interval > 0:
                            self._bybit_intervals[symbol] = interval
                    except (ValueError, TypeError):
                        pass

            # Count intervals
            interval_counts: Dict[float, int] = {}
            for interval in self._bybit_intervals.values():
                interval_counts[interval] = interval_counts.get(interval, 0) + 1
            logger.info("Bybit intervals: %s (fetched %d)", dict(sorted(interval_counts.items())), len(self._bybit_intervals))
        except Exception as e:
            logger.warning("Failed to build Bybit intervals: %s", e)

    def _get_bybit_interval(self, raw_symbol: str) -> float:
        """Get funding interval for Bybit symbol, default to 8.0 if unknown."""
        return self._bybit_intervals.get(raw_symbol, 8.0)

    # =========================================================================
    # Paradex: Dynamic Period -> 8h Display
    # =========================================================================
    
    async def _run_paradex_stream(self) -> None:
        """
        Paradex WebSocket stream for funding data.
        
        VERIFIED 2026-01-21:
        - Different assets have different funding_period_hours (1h, 2h, 4h, 8h)
        - WebSocket funding_rate field returns rate for that specific period
        - Website displays 8h rate
        - Convert: rate_8h = rate_period * (8 / period)
        """
        logger.info("Starting Paradex funding stream...")
        
        async def on_funding(channel: str, data: Dict[str, Any]) -> None:
            raw_symbol = data.get("market", "")
            rate_str = data.get("funding_rate")
            if not raw_symbol or rate_str is None:
                return
            try:
                rate_period = float(rate_str)
            except (ValueError, TypeError):
                return
            
            normalized = self._symbol_maps.get("Paradex", {}).get(raw_symbol)
            if not normalized:
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    return
            
            # Get asset-specific funding period and convert to 8h rate
            period_hours = self._get_paradex_period(raw_symbol)
            rate_8h = rate_period * (8.0 / period_hours)
            self.store.update_rate("Paradex", normalized, rate_8h, interval_hours=8.0)
            
            if normalized in DEBUG_SYMBOLS:
                logger.info("[Paradex] %s: %gh=%.8f -> 8h=%.6f%%", normalized, period_hours, rate_period, rate_8h * 100)

        try:
            await self._paradex.stream_funding_data(callback=on_funding)
        except Exception as e:
            logger.error("Paradex stream error: %s", e)
            while self._running:
                await self._poll_paradex_once()
                await asyncio.sleep(self.poll_interval_s)

    async def _poll_paradex_once(self) -> None:
        """Fallback REST polling for Paradex."""
        try:
            url = f"{self._paradex.REST_BASE}/funding/all"
            data = await self._paradex._get_json(url, timeout_s=15.0)
            results = data.get("results", []) if isinstance(data, dict) else []
            for item in results:
                raw_symbol = item.get("market", item.get("symbol", ""))
                rate_str = item.get("funding_rate")
                if not raw_symbol or rate_str is None:
                    continue
                try:
                    rate_period = float(rate_str)
                except (ValueError, TypeError):
                    continue
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    continue
                # Convert to 8h rate using asset-specific period
                period_hours = self._get_paradex_period(raw_symbol)
                rate_8h = rate_period * (8.0 / period_hours)
                self.store.update_rate("Paradex", normalized, rate_8h, interval_hours=8.0)
                if normalized in DEBUG_SYMBOLS:
                    logger.info("[Paradex] %s: %gh=%.8f -> 8h=%.6f%%", normalized, period_hours, rate_period, rate_8h * 100)
        except Exception as e:
            logger.warning("Paradex REST poll error: %s", e)

    # =========================================================================
    # Aster: 8h Rate (Binance-style)
    # =========================================================================
    
    async def _run_aster_stream(self) -> None:
        """
        Aster WebSocket stream for mark price + funding.
        Standard 8h rate (Binance-style).
        """
        logger.info("Starting Aster markPrice stream...")
        aster_symbols = list(self._symbol_maps.get("Aster", {}).keys())
        aster_symbols = [s for s in aster_symbols if s.isupper()]
        
        if not aster_symbols:
            logger.warning("No Aster symbols to stream, falling back to REST")
            while self._running:
                await self._poll_aster_once()
                await asyncio.sleep(self.poll_interval_s)
            return

        async def on_mark_price(msg: Dict[str, Any]) -> None:
            data = msg.get("data", msg)
            raw_symbol = data.get("s", data.get("symbol", ""))
            rate_str = data.get("r", data.get("lastFundingRate"))
            if not raw_symbol or rate_str is None:
                return
            try:
                raw_rate = float(rate_str)
            except (ValueError, TypeError):
                return
            
            normalized = self._symbol_maps.get("Aster", {}).get(raw_symbol)
            if not normalized:
                normalized = self._symbol_maps.get("Aster", {}).get(raw_symbol.upper())
            if not normalized:
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    return
            
            # Aster: DYNAMIC interval per symbol
            interval_hours = self._get_aster_interval(raw_symbol)
            self.store.update_rate("Aster", normalized, raw_rate, interval_hours=interval_hours)
            if normalized in DEBUG_SYMBOLS:
                logger.info("[Aster] %s: Raw=%.8f (Intv=%.1fh) -> 1h=%.8f", normalized, raw_rate, interval_hours, raw_rate / interval_hours)

        try:
            batch_size = 50
            for i in range(0, len(aster_symbols), batch_size):
                batch = aster_symbols[i:i + batch_size]
                asyncio.create_task(self._aster.stream_mark_price(batch, on_message=on_mark_price))
                await asyncio.sleep(0.5)
            while self._running:
                await asyncio.sleep(60)
        except Exception as e:
            logger.error("Aster stream error: %s", e)
            while self._running:
                await self._poll_aster_once()
                await asyncio.sleep(self.poll_interval_s)

    async def _poll_aster_once(self) -> None:
        """Fallback REST polling for Aster premiumIndex."""
        try:
            url = "https://fapi.asterdex.com/fapi/v1/premiumIndex"
            data = await self._aster._get_json(url, timeout_s=15.0)
            if not isinstance(data, list):
                return
            for item in data:
                raw_symbol = item.get("symbol", "")
                rate_str = item.get("lastFundingRate")
                if not raw_symbol or rate_str is None:
                    continue
                try:
                    raw_rate = float(rate_str)
                except (ValueError, TypeError):
                    continue
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    continue
                # Aster: DYNAMIC interval per symbol
                interval_hours = self._get_aster_interval(raw_symbol)
                self.store.update_rate("Aster", normalized, raw_rate, interval_hours=interval_hours)
                if normalized in DEBUG_SYMBOLS:
                    logger.info("[Aster] %s: Raw=%.8f (Intv=%.1fh) -> 1h=%.8f", normalized, raw_rate, interval_hours, raw_rate / interval_hours)
        except Exception as e:
            logger.warning("Aster REST poll error: %s", e)

    # =========================================================================
    # Omni: % Rate, Dynamic Interval
    # =========================================================================
    
    async def _run_omni_poller(self) -> None:
        """
        Omni REST polling for funding rates.
        
        VERIFIED 2026-01-21:
        - API returns ANNUALIZED rate in decimal form (e.g., 0.1095 = 10.95% APR)
        - Interval is DYNAMIC per asset (ETH=8h, BERA=2h, RESOLV=1h)
        - Conversion: period_rate = apr / (365 * 24 / interval_hours)
        """
        logger.info("Starting Omni REST poller (interval=%ds)...", self.poll_interval_s)
        
        while self._running:
            try:
                markets = await self._omni.fetch_markets()
                for m in markets:
                    ticker = m.get("ticker", "")
                    rate_str = m.get("funding_rate")
                    interval_s = m.get("funding_interval_s", 28800)
                    if not ticker or rate_str is None:
                        continue
                    try:
                        # Omni returns APR in decimal form (0.1095 = 10.95% APR)
                        apr_decimal = float(rate_str)
                        interval_hours = float(interval_s) / 3600.0
                    except (ValueError, TypeError):
                        continue
                    
                    # Safety: prevent divide by zero
                    if interval_hours <= 0:
                        continue

                    normalized = normalize_symbol(ticker)
                    if not normalized:
                        continue
                    
                    # Convert APR to period rate
                    periods_per_year = 365.0 * 24.0 / interval_hours
                    raw_rate = apr_decimal / periods_per_year
                    
                    # Store all rates without filtering (user wants to see all raw data)
                    self.store.update_rate("Omni", normalized, raw_rate, interval_hours=interval_hours)
                    
                    if normalized in DEBUG_SYMBOLS:
                        logger.info("[Omni] %s: APR=%.4f%% (Intv=%.1fh) -> Period=%.6f%%", 
                                   normalized, apr_decimal * 100, interval_hours, raw_rate * 100)
            except Exception as e:
                logger.warning("Omni poll error: %s", e)
            await asyncio.sleep(self.poll_interval_s)

    # =========================================================================
    # Lighter: 8h Rate
    # =========================================================================
    
    async def _run_lighter_poller(self) -> None:
        """
        Lighter REST polling for funding rates.

        VERIFIED 2026-01-28:
        - API returns 8-hour aggregated funding rate
        - Must divide by 8 to get the 1-hour rate shown on website
        - Payment interval: 1 hour
        - Website displays 1-hour rate with range [-0.5%, +0.5%]
        - Formula: fundingRate = (premium / 8) + interestRate
          -> This is the 1h rate formula, but API returns 8h aggregate

        Evidence:
        - AXS: API=-0.0134, website=-0.173% → -0.0134/8=-0.001675=-0.1675% ✓

        Note: API aggregates rates from multiple sources (exchange field):
        - exchange=lighter: Lighter's own rate
        - exchange=binance/bybit: Reference rates from CEXs (for comparison)
        """
        logger.info("Starting Lighter REST poller (interval=%ds)...", self.poll_interval_s)

        while self._running:
            try:
                rates = await self._lighter.fetch_funding_rates()
                for r in rates:
                    raw_symbol = r.get("symbol", "")
                    exchange_source = r.get("exchange", "")
                    rate_val = r.get("rate")

                    # Only use Lighter's own rates (not Binance/Bybit references)
                    if exchange_source != "lighter":
                        continue

                    if not raw_symbol or rate_val is None:
                        continue
                    try:
                        api_rate = float(rate_val)
                    except (ValueError, TypeError):
                        continue
                    normalized = normalize_symbol(raw_symbol)
                    if not normalized:
                        continue

                    # Convert 8h aggregate to 1h rate (what website displays)
                    rate_1h = api_rate / 8.0
                    self.store.update_rate("Lighter", normalized, rate_1h, interval_hours=1.0)

                    if normalized in DEBUG_SYMBOLS:
                        logger.info("[Lighter] %s: API_8h=%.8f → 1h=%.6f%%",
                                   normalized, api_rate, rate_1h * 100)

                logger.debug("Lighter poll: updated %d rates", len(rates))
            except Exception as e:
                logger.warning("Lighter poll error: %s", e)
            await asyncio.sleep(self.poll_interval_s)

    # =========================================================================
    # Hyperliquid: 1h Rate (Hourly Payment)
    # =========================================================================
    
    async def _run_hyperliquid_poller(self) -> None:
        """
        Hyperliquid REST polling for funding rates.
        
        VERIFIED 2026-01-21:
        - POST /info with type="metaAndAssetCtxs"
        - Returns 'funding' field = 1h rate (decimal form)
        - Payment: hourly (1/8 of 8h calculated rate)
        - interval_hours = 1.0
        """
        logger.info("Starting Hyperliquid REST poller (interval=%ds)...", self.poll_interval_s)
        
        while self._running:
            try:
                markets = await self._hyperliquid.fetch_markets()
                for m in markets:
                    raw_symbol = m.get("symbol", "")
                    funding_str = m.get("funding")
                    if not raw_symbol or funding_str is None:
                        continue
                    try:
                        rate_1h = float(funding_str)
                    except (ValueError, TypeError):
                        continue
                    
                    normalized = normalize_symbol(raw_symbol)
                    if not normalized:
                        continue
                    # Hyperliquid: API returns 1h rate directly
                    self.store.update_rate("Hyperliquid", normalized, rate_1h, interval_hours=1.0)
                    
                    if normalized in DEBUG_SYMBOLS:
                        logger.info("[Hyperliquid] %s: 1h=%.6f%%", normalized, rate_1h * 100)
                
                logger.debug("Hyperliquid poll: updated %d rates", len(markets))
            except Exception as e:
                logger.warning("Hyperliquid poll error: %s", e)
            await asyncio.sleep(self.poll_interval_s)

    # =========================================================================
    # Binance: Dynamic Interval (8h/4h) -> Display
    # =========================================================================

    def _cex_ws_symbols(self, dex_name: str) -> List[str]:
        """Return de-duplicated canonical symbols for CEX WS subscription.

        Why: older versions of symbol_map may contain both raw and raw.lower() keys.
        Subscribing keys() would double the list and can break WS (too many topics / long URL).
        """
        mp = self._symbol_maps.get(dex_name, {})
        if not mp:
            return []
        # Prefer uppercase raw symbols only
        syms = [s for s in mp.keys() if isinstance(s, str) and s and s.upper() == s]
        # If for some reason nothing is uppercase, fall back to all keys (still de-dupe)
        if not syms:
            syms = [s for s in mp.keys() if isinstance(s, str) and s]
        return sorted(set(syms))

    async def _run_binance_stream(self) -> None:
        """
        Binance WebSocket stream for mark price and funding rate.

        Fixes:
        - Subscribe only canonical (uppercase) symbols (avoid raw.lower() duplicates)
        - Batch subscriptions to avoid overly long combined-stream URL
        """
        logger.info("Starting Binance markPrice stream...")
        if not self._binance:
            logger.error("Binance connector not initialized")
            return

        binance_symbols = self._cex_ws_symbols("Binance")

        if not binance_symbols:
            logger.warning("No Binance symbols in symbol_map, using REST polling only")
            while self._running:
                await self._poll_binance_once()
                await asyncio.sleep(self.poll_interval_s)
            return

        async def on_mark_price(msg: Dict[str, Any]) -> None:
            raw_symbol = msg.get("s", "")
            rate_str = msg.get("r", "")
            if not raw_symbol or not rate_str:
                return
            try:
                rate_interval = float(rate_str)
            except (ValueError, TypeError):
                return

            normalized = self._symbol_maps.get("Binance", {}).get(raw_symbol)
            if not normalized:
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    return

            interval_hours = self._get_binance_interval(raw_symbol)
            self.store.update_rate("Binance", normalized, rate_interval, interval_hours=interval_hours)

            if normalized in DEBUG_SYMBOLS:
                logger.info("[Binance] %s: %gh=%.8f -> 1h=%.8f", normalized, interval_hours, rate_interval, rate_interval / interval_hours)

        # Conservative batch size to avoid URL/proxy limitations
        batch_size = 150
        batches = [binance_symbols[i:i + batch_size] for i in range(0, len(binance_symbols), batch_size)]
        logger.info("Binance WS: subscribing %d symbols across %d connections (batch=%d)", len(binance_symbols), len(batches), batch_size)

        first_tick = {"seen": False}

        async def on_mark_price_first(msg: Dict[str, Any]) -> None:
            if not first_tick["seen"]:
                first_tick["seen"] = True
                logger.info("Binance WS: first tick received")
            await on_mark_price(msg)

        # Spawn WS tasks per batch
        for bi, batch in enumerate(batches):
            t = asyncio.create_task(
                self._binance.stream_mark_price(batch, on_message=on_mark_price_first),
                name=f"binance_ws_{bi}"
            )
            self._tasks.append(t)
            await asyncio.sleep(0.2)

        # Keep this supervisor task alive
        while self._running:
            await asyncio.sleep(60)

    async def _poll_binance_once(self) -> None:
        """Fallback REST polling for Binance."""
        try:
            rates = await self._binance.fetch_funding_rates()
            if not rates:
                logger.warning("Binance: fetch_funding_rates returned empty list")
                return

            updated_count = 0
            for item in rates:
                raw_symbol = item.get("symbol", "")
                rate_str = item.get("lastFundingRate")
                if not raw_symbol or rate_str is None:
                    continue
                try:
                    rate_interval = float(rate_str)
                except (ValueError, TypeError):
                    continue

                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    continue

                # Get asset-specific funding interval
                interval_hours = self._get_binance_interval(raw_symbol)
                self.store.update_rate("Binance", normalized, rate_interval, interval_hours=interval_hours)
                updated_count += 1

                if normalized in DEBUG_SYMBOLS:
                    logger.info("[Binance REST] %s: %gh=%.8f -> 1h=%.8f", normalized, interval_hours, rate_interval, rate_interval / interval_hours)

            if updated_count > 0:
                logger.debug("Binance REST poll: updated %d rates", updated_count)
        except Exception as e:
            logger.warning("Binance REST poll error: %s", e, exc_info=True)

    # =========================================================================
    # Bybit: 8h Rate
    # =========================================================================

    async def _run_bybit_stream(self) -> None:
        """
        Bybit WebSocket stream for ticker funding rate.

        Fixes:
        - Subscribe only canonical (uppercase) symbols (avoid raw.lower() duplicates)
        - Batch subscriptions to reduce payload size (some environments choke on huge subscribe args)
        """
        logger.info("Starting Bybit ticker.fundingRate stream...")
        if not self._bybit:
            logger.error("Bybit connector not initialized")
            return

        bybit_symbols = self._cex_ws_symbols("Bybit")

        if not bybit_symbols:
            logger.warning("No Bybit symbols in symbol_map, using REST polling only")
            while self._running:
                await self._poll_bybit_once()
                await asyncio.sleep(self.poll_interval_s)
            return

        async def on_ticker_funding(msg: Dict[str, Any]) -> None:
            raw_symbol = msg.get("symbol", "")
            rate_str = msg.get("fundingRate")
            if not raw_symbol or rate_str is None:
                return
            try:
                raw_rate = float(rate_str)
            except (ValueError, TypeError):
                return

            normalized = self._symbol_maps.get("Bybit", {}).get(raw_symbol)
            if not normalized:
                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    return

            # Bybit: DYNAMIC interval per symbol (1h/2h/4h/8h)
            # Try to get from WebSocket message first, then fall back to cached value
            interval_str = msg.get("fundingIntervalHour")
            if interval_str:
                try:
                    interval_hours = float(interval_str)
                except (ValueError, TypeError):
                    interval_hours = self._get_bybit_interval(raw_symbol)
            else:
                interval_hours = self._get_bybit_interval(raw_symbol)

            self.store.update_rate("Bybit", normalized, raw_rate, interval_hours=interval_hours)

            if normalized in DEBUG_SYMBOLS:
                logger.info("[Bybit] %s: %.0fh=%.8f -> 1h=%.8f", normalized, interval_hours, raw_rate, raw_rate / interval_hours)

        batch_size = 100
        batches = [bybit_symbols[i:i + batch_size] for i in range(0, len(bybit_symbols), batch_size)]
        logger.info("Bybit WS: subscribing %d symbols across %d connections (batch=%d)", len(bybit_symbols), len(batches), batch_size)

        first_tick = {"seen": False}

        async def on_ticker_funding_first(msg: Dict[str, Any]) -> None:
            if not first_tick["seen"]:
                first_tick["seen"] = True
                logger.info("Bybit WS: first tick received")
            await on_ticker_funding(msg)

        for bi, batch in enumerate(batches):
            t = asyncio.create_task(
                self._bybit.stream_ticker_funding(batch, on_message=on_ticker_funding_first),
                name=f"bybit_ws_{bi}"
            )
            self._tasks.append(t)
            await asyncio.sleep(0.2)

        while self._running:
            await asyncio.sleep(60)

    async def _poll_bybit_once(self) -> None:
        """Fallback REST polling for Bybit."""
        try:
            markets = await self._bybit.fetch_markets()
            if not markets:
                logger.warning("Bybit: fetch_markets returned empty list")
                return

            updated_count = 0
            for m in markets:
                raw_symbol = m.get("symbol", "")
                rate_str = m.get("fundingRate")
                if not raw_symbol or rate_str is None:
                    continue
                try:
                    raw_rate = float(rate_str)
                except (ValueError, TypeError):
                    continue

                normalized = normalize_symbol(raw_symbol)
                if not normalized:
                    continue

                # Bybit: DYNAMIC interval per symbol (1h/2h/4h/8h)
                interval_str = m.get("fundingIntervalHour")
                if interval_str:
                    try:
                        interval_hours = float(interval_str)
                    except (ValueError, TypeError):
                        interval_hours = self._get_bybit_interval(raw_symbol)
                else:
                    interval_hours = self._get_bybit_interval(raw_symbol)

                self.store.update_rate("Bybit", normalized, raw_rate, interval_hours=interval_hours)
                updated_count += 1

                if normalized in DEBUG_SYMBOLS:
                    logger.info("[Bybit REST] %s: %.0fh=%.8f -> 1h=%.8f", normalized, interval_hours, raw_rate, raw_rate / interval_hours)

            if updated_count > 0:
                logger.debug("Bybit REST poll: updated %d rates", updated_count)
        except Exception as e:
            logger.warning("Bybit REST poll error: %s", e, exc_info=True)

    # =========================================================================
    # Periodic Interval Refresher
    # =========================================================================

    async def _run_interval_refresher(self) -> None:
        """
        Periodically refresh funding interval caches for CEXs.

        Binance and Bybit can dynamically adjust funding intervals (1h/4h/8h).
        This task refreshes the interval caches every 10 minutes to catch these changes.

        Maximum delay: ~10 minutes from when exchange changes interval to when we detect it.
        """
        refresh_interval_seconds = 600  # Refresh every 10 minutes

        logger.info("Starting interval refresher (refresh every %ds)...", refresh_interval_seconds)

        while self._running:
            await asyncio.sleep(refresh_interval_seconds)

            if not self._running:
                break

            try:
                logger.info("Refreshing funding interval caches...")

                # Refresh Binance intervals
                await self._build_binance_intervals()

                # Refresh Bybit intervals
                await self._build_bybit_intervals()

                logger.info("Interval caches refreshed successfully")
            except Exception as e:
                logger.warning("Failed to refresh interval caches: %s", e)

    # =========================================================================
    # Arbitrage Calculation
    # =========================================================================

    def find_opportunities(
        self,
        min_cashflow_10k: float = 1.0,
        min_dex_count: int = 2,
    ) -> List[ArbitrageOpportunity]:
        opportunities: List[ArbitrageOpportunity] = []
        # Iterate all symbols in store (not just common_assets) to see all raw data
        for symbol in self.store.get_symbols():
            spread_data = self.store.find_spread(symbol)
            if spread_data is None:
                continue
            long_dex, short_dex, spread_1h, min_rate, max_rate = spread_data
            coverage = self.store.get_symbol_coverage(symbol)
            if len(coverage) < min_dex_count:
                continue
            cashflow_10k_1h = 10000.0 * spread_1h
            apr = spread_1h * 24.0 * 365.0
            if cashflow_10k_1h < min_cashflow_10k:
                continue
            
            # Get display rates (original interval rates)
            long_entry = self.store.get_rate(symbol, long_dex)
            short_entry = self.store.get_rate(symbol, short_dex)
            
            long_rate_display = long_entry.get_display_rate() if long_entry else min_rate
            short_rate_display = short_entry.get_display_rate() if short_entry else max_rate
            long_interval = long_entry.get_interval_label() if long_entry else "?"
            short_interval = short_entry.get_interval_label() if short_entry else "?"
            
            opportunities.append(ArbitrageOpportunity(
                symbol=symbol,
                long_dex=long_dex,
                short_dex=short_dex,
                spread_1h=spread_1h,
                cashflow_10k_1h=cashflow_10k_1h,
                apr=apr,
                long_rate_1h=min_rate,
                short_rate_1h=max_rate,
                long_rate_display=long_rate_display,
                short_rate_display=short_rate_display,
                long_interval=long_interval,
                short_interval=short_interval,
            ))
        opportunities.sort(key=lambda x: x.cashflow_10k_1h, reverse=True)
        return opportunities
    
    def get_stats(self) -> Dict[str, Any]:
        store_stats = self.store.stats()
        stale = self.store.get_stale_threshold(max_age_seconds=120.0)
        return {
            "running": self._running,
            "common_assets_count": len(self.common_assets),
            "store": store_stats,
            "stale_dexs": [dex for dex, is_stale in stale.items() if is_stale],
        }

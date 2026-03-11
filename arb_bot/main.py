"""
Funding Rate Arbitrage Radar - Main Entry Point (Headless Mode)

Step 1: Fetch markets from all DEXs, build common asset list
Step 2: Start hybrid scanner (WSS + REST)
Step 3: Position monitoring
Step 4: Export data to dashboard_data.json for GUI consumption
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import aiohttp

from arb_bot.connectors import AsterConnector, BinanceConnector, BybitConnector, HyperliquidConnector, LighterConnector, OmniConnector, ParadexConnector
from arb_bot.core.normalizer import get_common_assets, normalize_symbol
from arb_bot.core.scanner import ArbitrageScanner, ArbitrageOpportunity
from arb_bot.core.store import MarketDataStore
from arb_bot.core.monitor import PositionMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arb_bot")

# Dashboard data file path
DASHBOARD_DATA_FILE = Path(__file__).parent / "dashboard_data.json"


def _extract_symbols(dex_name: str, markets: List[Dict[str, Any]]) -> List[str]:
    """Extract raw symbols/tickers from each DEX's raw market payload."""
    out: List[str] = []
    if not markets:
        return out

    if dex_name == "Omni":
        for m in markets:
            v = m.get("ticker")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    if dex_name == "Lighter":
        for m in markets:
            for k in ("symbol", "market_symbol", "marketSymbol", "asset", "asset_name", "assetName", "name"):
                v = m.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
        return out

    if dex_name == "Paradex":
        for m in markets:
            v = m.get("symbol")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    if dex_name == "Aster":
        for m in markets:
            v = m.get("symbol")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    # Binance: Extract "symbol" field (e.g., "BTCUSDT")
    if dex_name == "Binance":
        for m in markets:
            v = m.get("symbol")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    # Bybit: Extract "symbol" field (e.g., "BTCUSDT")
    if dex_name == "Bybit":
        for m in markets:
            v = m.get("symbol")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    # Fallback: try "symbol"
    for m in markets:
        v = m.get("symbol")
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


async def fetch_common_assets() -> Set[str]:
    """
    Step 1: Fetch markets from all DEXs and build common asset list.
    
    Returns:
        Set of normalized symbols that exist on at least 2 DEXs
    """
    logger.info("=== Step 1: Fetching markets from all DEXs ===")
    
    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        connectors = [
            OmniConnector(session),
            LighterConnector(session),
            ParadexConnector(session),
            AsterConnector(session),
            HyperliquidConnector(session),
            BinanceConnector(session),
            BybitConnector(session),
        ]

        tasks = [c.fetch_markets() for c in connectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    dex_to_markets: Dict[str, List[Dict[str, Any]]] = {}
    for c, r in zip(connectors, results):
        if isinstance(r, Exception):
            logger.warning("%s: fetch failed - %s", c.name, r)
            dex_to_markets[c.name] = []
        else:
            dex_to_markets[c.name] = r

    # Extract raw symbols
    dex_to_raw_symbols: Dict[str, List[str]] = {}
    for dex, markets in dex_to_markets.items():
        dex_to_raw_symbols[dex] = _extract_symbols(dex, markets)

    # Log summary
    for dex in ["Omni", "Lighter", "Paradex", "Aster", "Hyperliquid", "Binance", "Bybit"]:
        count = len(dex_to_markets.get(dex, []))
        logger.info("%s: %d markets", dex, count)

    # Build common assets (>= 2 DEXs)
    common_assets = get_common_assets(dex_to_raw_symbols)
    logger.info("Found %d common assets for arbitrage", len(common_assets))
    
    return set(common_assets.keys())


def write_dashboard_data(data: Dict[str, Any]) -> None:
    """
    Atomically write dashboard data to JSON file.
    
    Uses temp file + os.replace() to prevent read/write conflicts.
    """
    try:
        # Write to temp file first
        fd, temp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="dashboard_",
            dir=DASHBOARD_DATA_FILE.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            
            # Atomic replace
            os.replace(temp_path, DASHBOARD_DATA_FILE)
        except Exception:
            # Cleanup temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
            
    except Exception as e:
        logger.error("Failed to write dashboard data: %s", e)


def build_dashboard_data(
    scanner: ArbitrageScanner,
    monitor: PositionMonitor,
    opportunities: List[ArbitrageOpportunity],
) -> Dict[str, Any]:
    """Build the complete dashboard data structure."""

    # Get scanner stats
    stats = scanner.get_stats()
    store_stats = stats.get("store", {})

    # Get position statuses (pass store to new monitor API)
    position_statuses = monitor.get_positions_status(scanner.store)
    
    # Convert opportunities to dicts
    opportunities_data = []
    for opp in opportunities[:50]:  # Limit to top 50
        opportunities_data.append({
            "symbol": opp.symbol,
            "long_dex": opp.long_dex,
            "short_dex": opp.short_dex,
            "spread_1h": opp.spread_1h,
            "cashflow_10k_1h": opp.cashflow_10k_1h,
            "apr": opp.apr,
            "long_rate_1h": opp.long_rate_1h,
            "short_rate_1h": opp.short_rate_1h,
            "long_rate_display": opp.long_rate_display,
            "short_rate_display": opp.short_rate_display,
            "long_interval": opp.long_interval,
            "short_interval": opp.short_interval,
        })
    
    # NEW: Export raw rates for dashboard recalculation
    raw_rates = {}
    for symbol in scanner.store.get_symbols():
        rates = scanner.store.get_all_rates(symbol)
        raw_rates[symbol] = {
            dex: {
                "rate_1h": entry.rate_1h,
                "raw_rate": entry.raw_rate,
                "interval_hours": entry.interval_hours,
            }
            for dex, entry in rates.items()
        }
    
    # Count alerts (new monitor uses "status" field)
    critical_count = sum(1 for p in position_statuses if p.get("status") == "CRITICAL")
    warning_count = sum(1 for p in position_statuses if p.get("status") in ("WARNING", "NO_DATA"))
    
    return {
        "timestamp": time.time(),
        "timestamp_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        
        # Summary metrics
        "metrics": {
            "total_opportunities": len(opportunities),
            "active_positions": len(position_statuses),
            "critical_alerts": critical_count,
            "warning_alerts": warning_count,
            "total_symbols": store_stats.get("total_symbols", 0),
        },
        
        # DEX coverage
        "dex_coverage": store_stats.get("dex_symbol_counts", {}),
        "stale_dexs": stats.get("stale_dexs", []),
        
        # Opportunities (top 50)
        "opportunities": opportunities_data,
        
        # Raw rates for recalculation
        "raw_rates": raw_rates,
        
        # Position statuses
        "positions": position_statuses,
        
        # System status
        "system": {
            "running": stats.get("running", False),
            "common_assets_count": stats.get("common_assets_count", 0),
        },
    }


async def run_headless(common_assets: Set[str]) -> None:
    """
    Run the scanner in headless mode, exporting data to dashboard_data.json.
    """
    logger.info("=== Starting Headless Mode ===")
    
    store = MarketDataStore()
    scanner = ArbitrageScanner(
        common_assets=common_assets,
        store=store,
        poll_interval_s=15.0,  # Reduced from 60s to 15s for faster updates
    )

    # Initialize position monitor (new API: no store in constructor)
    monitor = PositionMonitor()
    
    try:
        await scanner.start()
    except Exception:
        # Ensure aiohttp session is closed on startup failure
        logger.exception("Scanner failed to start")
        await scanner.stop()
        raise
    
    # Wait for initial data
    logger.info("Waiting 10s for initial data...")
    await asyncio.sleep(10)
    
    update_interval = 5  # seconds
    
    try:
        while True:
            loop_start = time.time()
            
            # Reload positions (in case user edited positions.json)
            monitor.reload()
            
            # Find opportunities
            opportunities = scanner.find_opportunities(
                min_cashflow_10k=0.10,
                min_dex_count=2,
            )
            
            # Build and write dashboard data
            dashboard_data = build_dashboard_data(scanner, monitor, opportunities)
            write_dashboard_data(dashboard_data)
            
            # Log summary
            metrics = dashboard_data["metrics"]
            logger.info(
                "Update: %d opps | %d positions | %d critical | %d warning",
                metrics["total_opportunities"],
                metrics["active_positions"],
                metrics["critical_alerts"],
                metrics["warning_alerts"],
            )
            
            # Wait for next update
            elapsed = time.time() - loop_start
            sleep_time = max(0, update_interval - elapsed)
            await asyncio.sleep(sleep_time)
            
    except asyncio.CancelledError:
        pass
    finally:
        await scanner.stop()
        logger.info("Scanner stopped")


async def main() -> None:
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("  FUNDING RATE ARBITRAGE RADAR - HEADLESS MODE  ")
    logger.info("=" * 60)
    
    # Step 1: Get common assets
    common_assets = await fetch_common_assets()
    
    if not common_assets:
        logger.error("No common assets found! Check DEX connections.")
        return
    
    # Step 2 & 3: Run scanner in headless mode
    await run_headless(common_assets)


def cli():
    """CLI entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    cli()

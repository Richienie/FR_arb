"""
Compare funding rates across all DEXs to verify normalization.

IMPORTANT: This script must match the normalization logic in core/scanner.py.

Verified Rate Intervals (as of 2024-01):
- Omni: Variable via funding_interval_s (typically 8h), rate is in % form
- Lighter: 8h (FIXED - was incorrectly assumed to be 1h)
- Paradex: 8h
- Aster: 8h (Binance-style)
"""
import asyncio
import aiohttp


async def compare_rates():
    print("=" * 80)
    print("Cross-DEX Funding Rate Comparison (Normalized to 1h)")
    print("=" * 80)
    print("\nRate Interval Assumptions:")
    print("  - Omni: Variable (from funding_interval_s), rate in % form")
    print("  - Lighter: 8h (CORRECTED - NOT 1h as docs suggest)")
    print("  - Paradex: 8h")
    print("  - Aster: 8h")
    print("=" * 80)
    
    # Target assets to check
    targets = ["BTC", "ETH", "SOL", "BERA"]
    
    async with aiohttp.ClientSession() as session:
        # Fetch all data
        results = {}
        
        # 1. Omni
        print("\n[Fetching Omni...]")
        try:
            async with session.get(
                "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                for item in data.get("listings", []):
                    ticker = item.get("ticker", "")
                    if ticker in targets:
                        rate_pct = float(item.get("funding_rate", 0))
                        interval_s = float(item.get("funding_interval_s", 28800))
                        interval_h = interval_s / 3600
                        # Convert % to decimal, then to 1h
                        # IMPORTANT: Omni returns percentage (e.g., 0.01 means 0.01%)
                        raw_decimal = rate_pct / 100.0
                        rate_1h = raw_decimal / interval_h
                        results.setdefault(ticker, {})["Omni"] = {
                            "raw": rate_pct,
                            "unit": "%/period",
                            "interval_h": interval_h,
                            "rate_1h": rate_1h,
                        }
        except Exception as e:
            print(f"  Error: {e}")
        
        # 2. Lighter
        print("[Fetching Lighter...]")
        try:
            async with session.get(
                "https://mainnet.zklighter.elliot.ai/api/v1/funding-rates",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                for item in data.get("funding_rates", []):
                    symbol = item.get("symbol", "")
                    if symbol in targets:
                        rate = float(item.get("rate", 0))
                        # CORRECTED: Lighter is 8h rate, NOT 1h!
                        # This matches the fix in scanner.py
                        interval_h = 8.0
                        rate_1h = rate / interval_h
                        results.setdefault(symbol, {})["Lighter"] = {
                            "raw": rate,
                            "unit": "decimal/8h",
                            "interval_h": interval_h,
                            "rate_1h": rate_1h,
                        }
        except Exception as e:
            print(f"  Error: {e}")
        
        # 3. Paradex
        print("[Fetching Paradex...]")
        try:
            # Try funding endpoint first
            async with session.get(
                "https://api.prod.paradex.trade/v1/funding/all",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                for item in data.get("results", []):
                    raw_symbol = item.get("market", item.get("symbol", ""))
                    # Extract base asset (e.g., "BTC-USD-PERP" -> "BTC")
                    base = raw_symbol.split("-")[0] if "-" in raw_symbol else raw_symbol
                    if base in targets:
                        rate = float(item.get("funding_rate", 0))
                        interval_h = 8.0
                        rate_1h = rate / interval_h
                        results.setdefault(base, {})["Paradex"] = {
                            "raw": rate,
                            "unit": "decimal/8h",
                            "interval_h": interval_h,
                            "rate_1h": rate_1h,
                        }
        except Exception as e:
            print(f"  Error: {e}")
        
        # 4. Aster
        print("[Fetching Aster...]")
        try:
            async with session.get(
                "https://fapi.asterdex.com/fapi/v1/premiumIndex",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                for item in data:
                    symbol = item.get("symbol", "")
                    # Normalize symbol (remove USDT suffix)
                    base = symbol.replace("USDT", "").replace("USD", "")
                    if base in targets:
                        rate = float(item.get("lastFundingRate", 0))
                        # Aster is 8h rate (decimal form, like 0.0001)
                        interval_h = 8.0
                        rate_1h = rate / interval_h
                        results.setdefault(base, {})["Aster"] = {
                            "raw": rate,
                            "unit": "decimal/8h",
                            "interval_h": interval_h,
                            "rate_1h": rate_1h,
                        }
        except Exception as e:
            print(f"  Error: {e}")
        
        # Print comparison
        print("\n" + "=" * 80)
        print("RESULTS (Normalized 1h rates)")
        print("=" * 80)
        
        for ticker in targets:
            print(f"\n[{ticker}]")
            dex_data = results.get(ticker, {})
            if not dex_data:
                print("  No data")
                continue
            
            print(f"  {'DEX':<12} {'Raw':<15} {'Unit':<15} {'Interval':<10} {'Rate/1h':<15} {'APR':<12}")
            print("  " + "-" * 75)
            
            for dex, info in dex_data.items():
                raw = info["raw"]
                unit = info["unit"]
                interval = info["interval_h"]
                rate_1h = info["rate_1h"]
                apr = rate_1h * 24 * 365 * 100  # Convert to %
                
                print(f"  {dex:<12} {raw:<15.8f} {unit:<15} {interval:<10.1f} {rate_1h:<15.10f} {apr:<12.2f}%")
            
            # Calculate spread if multiple DEXs
            rates = [info["rate_1h"] for info in dex_data.values()]
            if len(rates) >= 2:
                spread = max(rates) - min(rates)
                spread_apr = spread * 24 * 365 * 100
                cashflow_10k = spread * 10000
                print(f"  -> Spread/1h: {spread:.10f} | APR: {spread_apr:.2f}% | $/h ($10k): ${cashflow_10k:.4f}")
        
        # Verification summary
        print("\n" + "=" * 80)
        print("VERIFICATION CHECKLIST")
        print("=" * 80)
        print("If Bot APR >> this script's APR for Lighter pairs, check scanner.py interval_hours!")
        print("Expected: All DEXs should show similar rate magnitudes for the same asset.")


if __name__ == "__main__":
    asyncio.run(compare_rates())

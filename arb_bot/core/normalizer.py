from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set


_QUOTE_SUFFIXES = (
    "USDT",
    "USDC",
    "USD",
)

# Only strip leading "W" for common wrapped majors; do NOT strip for WIF.
_WRAPPED_BASES = {"BTC", "ETH", "SOL"}


def normalize_symbol(raw_symbol: str) -> str:
    """
    Normalize a raw market symbol into the base asset ticker.

    Targets:
      BTC, ETH, SOL, WIF, ...

    Rules:
    - Remove common perp suffixes like "-USD-PERP", "-USDT-PERP", "-PERP"
    - Remove common quote suffixes like "USDT", "USDC", "USD"
    - Remove common quote segments like "-USD", "-USDT"
    - Strip leading "W" only for known wrapped majors (WBTC->BTC, WETH->ETH, WSOL->SOL)
    """
    if not raw_symbol:
        return ""

    s = raw_symbol.strip().upper()
    if not s:
        return ""

    # If it's dash-delimited, keep the left-most base token for common forms:
    # e.g. BTC-USD-PERP -> BTC, BTC-USD -> BTC
    if "-" in s:
        # Remove common endings first to avoid weird splits
        s = re.sub(r"-(USD|USDT|USDC)-PERP$", "", s)
        s = re.sub(r"-PERP$", "", s)
        s = re.sub(r"-(USD|USDT|USDC)$", "", s)
        s = s.split("-", 1)[0].strip()

    # Remove trailing perp tokens if still present (just in case)
    s = re.sub(r"(?:_)?PERP$", "", s).strip()

    # Strip common quote suffixes for concat symbols like BTCUSDT / ETHUSD
    for q in _QUOTE_SUFFIXES:
        if s.endswith(q) and len(s) > len(q):
            candidate = s[: -len(q)]
            # avoid stripping if result is too short or contains non-alnum
            if 2 <= len(candidate) <= 15 and re.fullmatch(r"[A-Z0-9]+", candidate or ""):
                s = candidate
                break

    # Strip leading "W" only for known wrapped majors (avoid WIF -> IF!)
    if s.startswith("W") and len(s) >= 3:
        maybe = s[1:]
        if maybe in _WRAPPED_BASES:
            s = maybe

    # Final sanity: keep alnum only (avoid weird separators)
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def get_common_assets(all_dex_data: Dict[str, Iterable[str]]) -> Dict[str, List[str]]:
    """
    Identify assets that exist on at least 2 DEXs.

    Input:
      { "Omni": ["BTC", "ETH", ...], "Paradex": ["BTC-USD-PERP", ...], ... }

    Output:
      { "BTC": ["Omni", "Lighter", ...], ... }  (only assets with >=2 venues)
    """
    asset_to_dexes: Dict[str, Set[str]] = {}

    for dex, symbols in all_dex_data.items():
        if not symbols:
            continue
        for raw in symbols:
            if not isinstance(raw, str):
                continue
            base = normalize_symbol(raw)
            if not base:
                continue
            asset_to_dexes.setdefault(base, set()).add(dex)

    common = {a: sorted(list(ds)) for a, ds in asset_to_dexes.items() if len(ds) >= 2}
    return common


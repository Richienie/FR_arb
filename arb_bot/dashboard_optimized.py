"""
Funding Rate Arbitrage Radar - Streamlit Dashboard (Ultra-Optimized with Fragment)

Performance optimizations:
1. Smart IO: Only reload JSON when file modification time changes
2. Fragment-based partial refresh: Only refresh live content, sidebar stays static
3. Atomic write protection: Backend uses temp file + os.replace()
4. Auto-refresh every 5 seconds WITHOUT full page reload

Requirements: Streamlit >= 1.37.0
Upgrade: pip install --upgrade streamlit

Run with: streamlit run arb_bot/dashboard_optimized.py
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# Add parent directory to path for imports when running via streamlit
_script_dir = Path(__file__).parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from arb_bot.core.monitor import PositionMonitor

# Constants
DASHBOARD_DATA_FILE = Path(__file__).parent / "dashboard_data.json"
FRAGMENT_REFRESH_INTERVAL = 5  # seconds - fragment auto-refresh
CACHE_TTL_SECONDS = 10

# Page config (must be first Streamlit command)
st.set_page_config(
    page_title="Funding Rate Arbitrage Radar",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# Session State Initialization
# =============================================================================

def init_session_state() -> None:
    """Initialize session state variables."""
    if "monitor" not in st.session_state:
        st.session_state.monitor = PositionMonitor()

    if "last_file_mtime" not in st.session_state:
        st.session_state.last_file_mtime = 0.0

    if "cached_data" not in st.session_state:
        st.session_state.cached_data = None


def get_monitor() -> PositionMonitor:
    """Get the PositionMonitor singleton instance."""
    return st.session_state.monitor


# =============================================================================
# Smart IO - Only load when file changes
# =============================================================================

def get_file_mtime(filepath: Path) -> float:
    """Get file modification time, return 0 if file doesn't exist."""
    try:
        return os.path.getmtime(filepath)
    except OSError:
        return 0.0


def load_dashboard_data_smart() -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    Smart loader: Only read JSON if file modification time changed.

    Returns:
        Tuple of (data, was_updated)
        - data: The dashboard data dict (or cached version)
        - was_updated: True if data was freshly loaded
    """
    current_mtime = get_file_mtime(DASHBOARD_DATA_FILE)

    # Check if file has been modified since last read
    if current_mtime > st.session_state.last_file_mtime:
        # File was updated, need to reload
        try:
            with open(DASHBOARD_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Update cache
            st.session_state.last_file_mtime = current_mtime
            st.session_state.cached_data = data
            return data, True

        except FileNotFoundError:
            return None, False
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON in dashboard data: {e}")
            return st.session_state.cached_data, False
        except Exception as e:
            st.error(f"Failed to load dashboard data: {e}")
            return st.session_state.cached_data, False

    # File hasn't changed, return cached data
    return st.session_state.cached_data, False


# =============================================================================
# Cached Calculations (Simplified - rely on mtime check)
# =============================================================================

def recalculate_opportunities(
    raw_rates: Dict[str, Dict[str, Any]],
    selected_dexs: List[str],
    min_spread: float = 0.0001,
) -> List[Dict[str, Any]]:
    """
    Recalculate opportunities with user filters.
    No caching needed - fast enough with mtime check.
    """
    opportunities = []

    for symbol, dex_rates in raw_rates.items():
        # Filter to selected DEXs
        filtered_rates = {
            dex: rates for dex, rates in dex_rates.items()
            if dex in selected_dexs
        }

        if len(filtered_rates) < 2:
            continue

        # Find min/max rates
        rate_1h_values = {dex: rates["rate_1h"] for dex, rates in filtered_rates.items()}
        long_dex = min(rate_1h_values, key=rate_1h_values.get)
        short_dex = max(rate_1h_values, key=rate_1h_values.get)

        long_rate_1h = rate_1h_values[long_dex]
        short_rate_1h = rate_1h_values[short_dex]

        spread_1h = short_rate_1h - long_rate_1h

        if spread_1h < min_spread:
            continue

        # Get display info
        long_info = filtered_rates[long_dex]
        short_info = filtered_rates[short_dex]

        opportunities.append({
            "symbol": symbol,
            "long_dex": long_dex,
            "short_dex": short_dex,
            "spread_1h": spread_1h,
            "cashflow_10k_1h": 10000.0 * spread_1h,
            "apr": spread_1h * 24.0 * 365.0,
            "long_rate_1h": long_rate_1h,
            "short_rate_1h": short_rate_1h,
            "long_rate_display": long_info["raw_rate"],
            "short_rate_display": short_info["raw_rate"],
            "long_interval": format_interval(long_info["interval_hours"]),
            "short_interval": format_interval(short_info["interval_hours"]),
        })

    # Sort by cashflow
    opportunities.sort(key=lambda x: x["cashflow_10k_1h"], reverse=True)
    return opportunities


def format_interval(hours: float) -> str:
    """Format interval hours as string."""
    if hours == 1.0:
        return "1h"
    elif hours == 4.0:
        return "4h"
    elif hours == 8.0:
        return "8h"
    else:
        return f"{hours:.1f}h"


def format_rate(rate: float) -> str:
    """Format rate as percentage string."""
    return f"{rate * 100:+.4f}%"


# =============================================================================
# UI Components
# =============================================================================

def render_sidebar(data: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Render sidebar with settings.
    This stays OUTSIDE the fragment, so it won't refresh.
    """
    with st.sidebar:
        st.header("⚙️ Settings")

        # Auto-refresh toggle (not needed anymore with fragment, but keep for manual control)
        auto_refresh = st.checkbox(
            "Auto-refresh",
            value=True,
            help=f"Fragment auto-refreshes every {FRAGMENT_REFRESH_INTERVAL}s"
        )

        st.divider()

        # DEX filter
        st.subheader("DEX Filter")

        if data and "store" in data:
            all_dexs = set()
            for rates in data["store"].values():
                all_dexs.update(rates.keys())
            all_dexs = sorted(all_dexs)

            selected_dexs = st.multiselect(
                "Select DEXs to include",
                options=all_dexs,
                default=all_dexs,
                help="Filter opportunities to selected DEXs only"
            )
        else:
            selected_dexs = []
            st.info("No data available")

        st.divider()

        # System info
        st.caption("📊 System Info")
        if data:
            ts = data.get("timestamp", 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                age = time.time() - ts
                st.caption(f"Last update: {dt.strftime('%H:%M:%S')}")
                st.caption(f"Age: {age:.1f}s")

        st.caption(f"Refresh: {FRAGMENT_REFRESH_INTERVAL}s")

    return auto_refresh, selected_dexs


def render_metrics(data: Dict[str, Any]) -> None:
    """Render key metrics."""
    metrics = data.get("metrics", {})

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "📡 Opportunities",
            metrics.get("total_opportunities", 0),
        )
    with col2:
        st.metric(
            "📊 Active Positions",
            metrics.get("active_positions", 0),
        )
    with col3:
        critical = metrics.get("critical_alerts", 0)
        st.metric(
            "🚨 Critical",
            critical,
            delta=None if critical == 0 else "Alert!",
            delta_color="inverse"
        )
    with col4:
        st.metric(
            "🌐 Total Symbols",
            metrics.get("total_symbols", 0),
        )


def render_dex_coverage(data: Dict[str, Any]) -> None:
    """Render DEX coverage info."""
    scanner_stats = data.get("scanner_stats", {})
    store_stats = scanner_stats.get("store", {})
    dex_counts = store_stats.get("dex_symbol_counts", {})

    if dex_counts:
        st.caption("DEX Coverage:")
        cols = st.columns(len(dex_counts))
        for col, (dex, count) in zip(cols, sorted(dex_counts.items(), key=lambda x: -x[1])):
            col.caption(f"**{dex}**: {count}")


def render_strategy_monitoring(data: Dict[str, Any]) -> None:
    """Render position monitoring section."""
    st.subheader("🎯 Strategy Monitoring")

    positions = data.get("positions", [])

    if not positions:
        st.info("No active positions. Add positions in `positions.json`.")
        return

    # Build DataFrame
    df_positions = pd.DataFrame(positions)

    # Status badges
    def status_badge(status: str) -> str:
        if status == "OK":
            return "🟢 OK"
        elif status == "WARNING":
            return "🟡 WARNING"
        elif status == "CRITICAL":
            return "🔴 CRITICAL"
        else:
            return "⚪ " + status

    df_positions["Status"] = df_positions["status"].apply(status_badge)

    # Display columns
    display_cols = ["Status", "symbol", "long_dex", "short_dex"]
    if "current_spread" in df_positions.columns:
        display_cols.append("current_spread")
    if "cashflow_per_10k" in df_positions.columns:
        display_cols.append("cashflow_per_10k")

    st.dataframe(
        df_positions[display_cols],
        use_container_width=True,
        hide_index=True,
    )


def render_opportunities_table(
    data: Dict[str, Any],
    selected_dexs: List[str],
) -> None:
    """Render opportunities table with filters."""
    raw_rates = data.get("store", {})

    if not raw_rates:
        st.warning("No funding rate data available")
        return

    if not selected_dexs:
        st.warning("Please select at least one DEX in the sidebar")
        return

    # Recalculate with filters
    opportunities = recalculate_opportunities(raw_rates, selected_dexs, min_spread=0.0001)

    if not opportunities:
        st.info("No opportunities found with current filters")
        return

    st.caption(f"Found {len(opportunities)} opportunities")

    # Build DataFrame
    df = pd.DataFrame(opportunities)

    # Format display
    df_display = df.copy()

    if 'long_rate_display' in df_display.columns:
        df_display["Long DEX"] = df_display.apply(
            lambda x: f"{x['long_dex']} ({format_rate(x['long_rate_display'])} {x['long_interval']})",
            axis=1
        )
        df_display["Short DEX"] = df_display.apply(
            lambda x: f"{x['short_dex']} ({format_rate(x['short_rate_display'])} {x['short_interval']})",
            axis=1
        )
    else:
        df_display["Long DEX"] = df_display["long_dex"]
        df_display["Short DEX"] = df_display["short_dex"]

    df_display["Spread (1h)"] = df_display["spread_1h"].apply(lambda x: f"{x * 100:.4f}%")
    df_display["💰 Profit/10k"] = df_display["cashflow_10k_1h"].apply(lambda x: f"${x:.2f}")
    df_display["APR"] = df_display["apr"].apply(lambda x: f"{x * 100:.2f}%")

    # Display
    st.dataframe(
        df_display[["symbol", "Long DEX", "Short DEX", "Spread (1h)", "💰 Profit/10k", "APR"]],
        use_container_width=True,
        hide_index=True,
    )


# =============================================================================
# FRAGMENT: Auto-refreshing live content
# =============================================================================

@st.fragment(run_every=FRAGMENT_REFRESH_INTERVAL)
def render_live_content(selected_dexs: List[str]):
    """
    Fragment that auto-refreshes every N seconds.
    Only this part refreshes - sidebar stays static!
    """
    # Load data (uses mtime check, very fast)
    data, was_updated = load_dashboard_data_smart()

    # Show update indicator
    if was_updated:
        st.toast("🔄 Data refreshed", icon="✅")

    # Handle no data case
    if data is None:
        st.warning(
            "⚠️ No dashboard data found. "
            "Make sure the backend is running: `python main.py`"
        )
        st.stop()

    # Render main components
    render_metrics(data)
    render_dex_coverage(data)

    st.divider()

    # Strategy Monitoring at TOP
    render_strategy_monitoring(data)

    st.divider()

    # Tabs for opportunities and details
    tab1, tab2 = st.tabs(["📡 Radar (New Opportunities)", "📊 Details"])

    with tab1:
        st.subheader("Top Arbitrage Opportunities")
        st.caption("Strategy: LONG on low-rate DEX, SHORT on high-rate DEX (delta-neutral)")
        render_opportunities_table(data, selected_dexs)

    with tab2:
        st.subheader("Raw Data")
        with st.expander("View Raw Dashboard Data"):
            st.json(data)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """
    Main dashboard function with fragment-based partial refresh.

    Key optimization: Only live content refreshes, sidebar stays static.
    """
    # Initialize session state
    init_session_state()

    # One-time data load for sidebar
    data, _ = load_dashboard_data_smart()

    # Render sidebar (OUTSIDE fragment - stays static)
    auto_refresh, selected_dexs = render_sidebar(data)

    # Main title (OUTSIDE fragment - stays static)
    st.title("📡 Funding Rate Arbitrage Radar")
    st.caption(f"⚡ Ultra-smooth refresh every {FRAGMENT_REFRESH_INTERVAL}s with @st.fragment")

    # Live content (INSIDE fragment - auto-refreshes)
    if auto_refresh:
        render_live_content(selected_dexs)
    else:
        # Manual mode: no auto-refresh
        st.info("Auto-refresh is disabled. Enable it in the sidebar.")
        # Still render content once
        data, _ = load_dashboard_data_smart()
        if data:
            render_metrics(data)
            render_dex_coverage(data)
            st.divider()
            render_strategy_monitoring(data)
            st.divider()

            tab1, tab2 = st.tabs(["📡 Radar (New Opportunities)", "📊 Details"])
            with tab1:
                st.subheader("Top Arbitrage Opportunities")
                render_opportunities_table(data, selected_dexs)
            with tab2:
                st.subheader("Raw Data")
                with st.expander("View Raw Dashboard Data"):
                    st.json(data)


if __name__ == "__main__":
    main()

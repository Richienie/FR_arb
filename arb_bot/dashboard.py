"""
Funding Rate Arbitrage Radar - Streamlit Dashboard (Optimized)

Performance optimizations:
1. Smart IO: Only reload JSON when file modification time changes
2. Caching: @st.cache_data for expensive calculations
3. Throttled refresh: 10-second interval

Run with: streamlit run arb_bot/dashboard.py
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
REFRESH_INTERVAL_SECONDS = 10
CACHE_TTL_SECONDS = 20

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
# Cached Calculations
# =============================================================================

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def recalculate_opportunities_cached(
    raw_rates_json: str,
    selected_dexs_tuple: Tuple[str, ...],
    min_spread: float = 0.0001,
) -> List[Dict[str, Any]]:
    """
    Recalculate arbitrage opportunities based on selected DEXs.

    Note: Parameters are serializable for caching (JSON string, tuple).
    """
    raw_rates = json.loads(raw_rates_json)
    selected_dexs = set(selected_dexs_tuple)
    opportunities = []

    for symbol, dex_rates in raw_rates.items():
        filtered_rates = {
            dex: rates
            for dex, rates in dex_rates.items()
            if dex in selected_dexs
        }

        if len(filtered_rates) < 2:
            continue

        min_item = min(filtered_rates.items(), key=lambda x: x[1]["rate_1h"])
        max_item = max(filtered_rates.items(), key=lambda x: x[1]["rate_1h"])

        long_dex_name, long_rates = min_item
        short_dex_name, short_rates = max_item

        if long_dex_name == short_dex_name:
            continue

        spread_1h = short_rates["rate_1h"] - long_rates["rate_1h"]

        if spread_1h < min_spread:
            continue

        cashflow_10k_1h = spread_1h * 10000
        apr = spread_1h * 24 * 365

        opportunities.append({
            "symbol": symbol,
            "long_dex": long_dex_name,
            "short_dex": short_dex_name,
            "spread_1h": spread_1h,
            "cashflow_10k_1h": cashflow_10k_1h,
            "apr": apr,
            "long_rate_1h": long_rates["rate_1h"],
            "short_rate_1h": short_rates["rate_1h"],
            "long_rate_display": long_rates["raw_rate"],
            "short_rate_display": short_rates["raw_rate"],
            "long_interval": format_interval(long_rates["interval_hours"]),
            "short_interval": format_interval(short_rates["interval_hours"]),
        })

    opportunities.sort(key=lambda x: x["cashflow_10k_1h"], reverse=True)
    return opportunities


# =============================================================================
# Formatting Utilities
# =============================================================================

def format_rate(rate: float) -> str:
    """Format rate as percentage."""
    return f"{rate * 100:+.4f}%"


def format_apr(apr: float) -> str:
    """Format APR as percentage."""
    return f"{apr * 100:.1f}%"


def format_usd(amount: float) -> str:
    """Format USD amount."""
    if amount >= 0:
        return f"${amount:,.2f}"
    return f"-${abs(amount):,.2f}"


def format_interval(hours: float) -> str:
    """Format interval label."""
    if hours == 1.0:
        return "1h"
    elif hours == 4.0:
        return "4h"
    elif hours == 8.0:
        return "8h"
    else:
        return f"{hours:.1f}h"


def format_timestamp(ts: float) -> str:
    """Format unix timestamp to readable string."""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return "N/A"


# =============================================================================
# Render Components
# =============================================================================

def render_metrics(data: Dict[str, Any]) -> None:
    """Render the top metrics row."""
    metrics = data.get("metrics", {})

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            label="📡 Opportunities",
            value=metrics.get("total_opportunities", 0),
        )

    with col2:
        st.metric(
            label="📦 Active Positions",
            value=metrics.get("active_positions", 0),
        )

    with col3:
        critical = metrics.get("critical_alerts", 0)
        st.metric(
            label="🚨 Critical Alerts",
            value=critical,
            delta=f"{critical} need action" if critical > 0 else None,
            delta_color="inverse" if critical > 0 else "off",
        )

    with col4:
        warning = metrics.get("warning_alerts", 0)
        st.metric(
            label="⚠️ Warnings",
            value=warning,
        )

    with col5:
        st.metric(
            label="🔗 Tracked Symbols",
            value=metrics.get("total_symbols", 0),
        )


def render_dex_coverage(data: Dict[str, Any]) -> None:
    """Render DEX coverage info."""
    coverage = data.get("dex_coverage", {})
    stale = data.get("stale_dexs", [])

    cols = st.columns(7)
    dex_names = ["Omni", "Lighter", "Paradex", "Aster", "Hyperliquid", "Binance", "Bybit"]

    for i, dex in enumerate(dex_names):
        with cols[i]:
            count = coverage.get(dex, 0)
            is_stale = dex in stale
            status = "🔴" if is_stale else "🟢"
            st.caption(f"{dex}: {count} {status}")


def render_opportunities_table(data: Dict[str, Any], selected_dexs: List[str]) -> None:
    """Render the opportunities table with cached recalculation."""
    raw_rates = data.get("raw_rates", {})

    if raw_rates and selected_dexs:
        # Use cached calculation
        raw_rates_json = json.dumps(raw_rates)
        selected_dexs_tuple = tuple(sorted(selected_dexs))

        opportunities = recalculate_opportunities_cached(
            raw_rates_json,
            selected_dexs_tuple,
        )

        if not opportunities:
            st.info("No opportunities found for selected DEXs.")
            return

        df = pd.DataFrame(opportunities)
    else:
        opportunities = data.get("opportunities", [])

        if not opportunities:
            st.info("No opportunities found. Waiting for data...")
            return

        df = pd.DataFrame(opportunities)

        if selected_dexs:
            df = df[
                (df["long_dex"].isin(selected_dexs)) &
                (df["short_dex"].isin(selected_dexs))
            ]

        if df.empty:
            st.info("No opportunities match the selected DEX filter.")
            return

    # Format display columns
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
        df_display["Long DEX"] = df_display.apply(
            lambda x: f"{x['long_dex']} ({format_rate(x['long_rate_1h'])})",
            axis=1
        )
        df_display["Short DEX"] = df_display.apply(
            lambda x: f"{x['short_dex']} ({format_rate(x['short_rate_1h'])})",
            axis=1
        )

    df_display["Spread/h"] = df["spread_1h"].apply(format_rate)
    df_display["$/h ($10k)"] = df["cashflow_10k_1h"].apply(format_usd)
    df_display["APR"] = df["apr"].apply(format_apr)

    df_display = df_display.rename(columns={"symbol": "Symbol"})
    columns = ["Symbol", "Long DEX", "Short DEX", "Spread/h", "$/h ($10k)", "APR"]

    st.dataframe(
        df_display[columns],
        use_container_width=True,
        hide_index=True,
        height=600,
    )


def render_strategy_monitoring(data: Dict[str, Any]) -> None:
    """
    Render the Strategy Watchdog monitoring panel.
    Shows active positions with alert banners for CRITICAL/WARNING status.
    """
    positions = data.get("positions", [])

    st.subheader("👀 Active Strategy Monitoring")

    if not positions:
        st.info("No active positions. Add one using the sidebar '🛡️ Strategy Watchdog' section.")
        return

    # Check for alerts
    critical_positions = [p for p in positions if p.get("status") == "CRITICAL"]
    warning_positions = [p for p in positions if p.get("status") == "WARNING"]
    no_data_positions = [p for p in positions if p.get("status") == "NO_DATA"]

    # Show alert banners
    if critical_positions:
        st.error(
            f"🚨 **CRITICAL ALERT**: {len(critical_positions)} position(s) have NEGATIVE CARRY! "
            "You are losing money. Consider closing these positions immediately."
        )
        for pos in critical_positions:
            st.error(
                f"**{pos['symbol']}** ({pos['long_dex']}/{pos['short_dex']}): {pos['message']}"
            )

    if warning_positions:
        st.warning(
            f"⚠️ **WARNING**: {len(warning_positions)} position(s) below threshold."
        )

    if no_data_positions:
        st.warning(
            f"📡 **NO DATA**: {len(no_data_positions)} position(s) missing market data."
        )

    # Build positions table
    df_data = []
    for pos in positions:
        status = pos.get("status", "OK")
        status_icons = {
            "CRITICAL": "🚨 CRITICAL",
            "WARNING": "⚠️ WARNING",
            "NO_DATA": "📡 NO DATA",
        }
        status_icon = status_icons.get(status, "✅ OK")

        current_spread = pos.get("current_spread")
        cashflow = pos.get("cashflow_per_10k")

        df_data.append({
            "Status": status_icon,
            "Symbol": pos.get("symbol", "?"),
            "Long DEX": pos.get("long_dex", "?"),
            "Short DEX": pos.get("short_dex", "?"),
            "Current Spread": format_rate(current_spread) if current_spread is not None else "N/A",
            "$/h ($10k)": format_usd(cashflow) if cashflow is not None else "N/A",
            "Threshold": format_rate(pos.get("alert_threshold", 0)),
            "Entry Spread": format_rate(pos["entry_spread"]) if pos.get("entry_spread") else "N/A",
            "Created": format_timestamp(pos.get("created_at", 0)),
            "ID": pos.get("id", "")[:8] + "...",
        })

    if df_data:
        st.dataframe(
            pd.DataFrame(df_data),
            use_container_width=True,
            hide_index=True,
        )

    # Remove position section
    with st.expander("🗑️ Remove Position"):
        position_options = {
            f"{p['symbol']} ({p['long_dex']}/{p['short_dex']}) - {p['id'][:8]}": p['id']
            for p in positions
        }

        if position_options:
            selected_label = st.selectbox(
                "Select position to remove",
                options=list(position_options.keys()),
                key="remove_position_select",
            )

            if st.button("Remove Selected Position", type="primary", key="remove_position_btn"):
                selected_id = position_options[selected_label]
                monitor = get_monitor()
                if monitor.remove_position(selected_id):
                    st.success(f"Removed position: {selected_label}")
                    st.rerun()
                else:
                    st.error("Failed to remove position")


def render_sidebar(data: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Render the sidebar with controls.

    Returns:
        Tuple of (auto_refresh, selected_dexs)
    """
    st.sidebar.title("📡 Arbitrage Radar")

    # Show last update time
    if data:
        timestamp_str = data.get("timestamp_str", "Unknown")
        st.sidebar.caption(f"Last update: {timestamp_str}")

    st.sidebar.divider()

    # === DEX Filter ===
    st.sidebar.subheader("🔍 DEX Filter")
    all_dexs = ["Omni", "Lighter", "Paradex", "Aster", "Hyperliquid", "Binance", "Bybit"]
    selected_dexs = st.sidebar.multiselect(
        "Select DEXs to monitor",
        options=all_dexs,
        default=all_dexs,
        help="Dynamic Recalculation: Finds best arbitrage pairs from selected DEXs",
        key="dex_filter",
    )

    st.sidebar.divider()

    # === Strategy Watchdog (Add Position) ===
    st.sidebar.subheader("🛡️ Strategy Watchdog")

    with st.sidebar.form("add_position_form", clear_on_submit=True):
        st.caption("Manually record a position to monitor")

        symbol = st.text_input(
            "Symbol",
            placeholder="ETH",
            help="e.g., ETH, BTC, SOL",
            key="form_symbol",
        )
        long_dex = st.selectbox(
            "Long DEX",
            options=all_dexs,
            index=1,  # Default to Lighter
            help="DEX where you are LONG (paying funding)",
            key="form_long_dex",
        )
        short_dex = st.selectbox(
            "Short DEX",
            options=all_dexs,
            index=2,  # Default to Paradex
            help="DEX where you are SHORT (receiving funding)",
            key="form_short_dex",
        )
        alert_threshold = st.number_input(
            "Alert Threshold (spread)",
            value=0.0005,
            step=0.0001,
            format="%.4f",
            help="Trigger WARNING when spread drops below this (e.g., 0.0005 = 0.05%)",
            key="form_threshold",
        )
        entry_spread = st.number_input(
            "Entry Spread (optional)",
            value=0.0,
            step=0.0001,
            format="%.4f",
            help="Spread when you opened the position (for tracking)",
            key="form_entry_spread",
        )

        submitted = st.form_submit_button("Add Position", use_container_width=True)

        if submitted:
            if not symbol:
                st.error("Symbol is required")
            elif long_dex == short_dex:
                st.error("Long and Short DEX must be different")
            else:
                monitor = get_monitor()
                position = monitor.add_position(
                    symbol=symbol.upper().strip(),
                    long_dex=long_dex,
                    short_dex=short_dex,
                    alert_threshold=alert_threshold,
                    entry_spread=entry_spread if entry_spread != 0 else None,
                )
                st.success(f"Added: {position.symbol} ({position.long_dex}/{position.short_dex})")
                st.rerun()

    # Show current position count
    monitor = get_monitor()
    positions = monitor.get_positions()
    st.sidebar.caption(f"Tracking {len(positions)} position(s)")

    st.sidebar.divider()

    # Refresh controls
    if st.sidebar.button("🔄 Refresh Now", use_container_width=True, key="refresh_btn"):
        monitor.reload()
        # Clear file mtime cache to force reload
        st.session_state.last_file_mtime = 0.0
        st.rerun()

    auto_refresh = st.sidebar.checkbox(
        f"Auto-refresh ({REFRESH_INTERVAL_SECONDS}s)",
        value=True,
        key="auto_refresh",
    )

    # Show cache status
    st.sidebar.caption(
        f"💾 Cache TTL: {CACHE_TTL_SECONDS}s | "
        f"File mtime: {st.session_state.last_file_mtime:.0f}"
    )

    return auto_refresh, selected_dexs


# =============================================================================
# Main Application
# =============================================================================

def main():
    """Main dashboard function with optimized data loading."""
    # Initialize session state
    init_session_state()

    # Smart load: only read file if modified
    data, was_updated = load_dashboard_data_smart()

    # Render sidebar (always render, even without data)
    auto_refresh, selected_dexs = render_sidebar(data)

    # Main title
    st.title("📡 Funding Rate Arbitrage Radar")

    # Show update indicator
    if was_updated:
        st.toast("Data refreshed", icon="🔄")

    # Handle no data case
    if data is None:
        st.warning(
            "No dashboard data found. "
            "Make sure the backend is running: `python -m arb_bot.main`"
        )
        if auto_refresh:
            time.sleep(REFRESH_INTERVAL_SECONDS)
            st.rerun()
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

    # Auto-refresh with throttled interval
    if auto_refresh:
        time.sleep(REFRESH_INTERVAL_SECONDS)
        st.rerun()


if __name__ == "__main__":
    main()

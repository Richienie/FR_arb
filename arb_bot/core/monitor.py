"""
Position Monitor - Track manually entered positions and monitor spread thresholds.

Responsibilities:
1. Load/save positions from positions.json
2. Check positions against live data from MarketDataStore
3. Generate alerts when spread drops below threshold or goes negative
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from arb_bot.core.store import MarketDataStore

logger = logging.getLogger(__name__)

# Status type alias
Status = Literal["OK", "WARNING", "CRITICAL", "NO_DATA"]


@dataclass
class Position:
    """
    A manually-entered arbitrage position.

    Strategy: Long on one DEX, Short on another DEX.
    Profit when: short_rate > long_rate (collect funding on short, pay less on long).
    """
    id: str  # UUID
    symbol: str  # e.g., "ETH"
    long_dex: str  # e.g., "Lighter"
    short_dex: str  # e.g., "Paradex"
    alert_threshold: float  # e.g., 0.0005 for 0.05% spread threshold
    entry_spread: Optional[float] = None  # Optional spread at entry time
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize position to dictionary."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "long_dex": self.long_dex,
            "short_dex": self.short_dex,
            "alert_threshold": self.alert_threshold,
            "entry_spread": self.entry_spread,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        """Deserialize position from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            symbol=data.get("symbol", ""),
            long_dex=data.get("long_dex", ""),
            short_dex=data.get("short_dex", ""),
            alert_threshold=float(data.get("alert_threshold", 0.0005)),
            entry_spread=data.get("entry_spread"),
            created_at=float(data.get("created_at", datetime.now().timestamp())),
        )


@dataclass
class PositionStatusResult:
    """Result of checking a position against live market data."""
    # Position info
    id: str
    symbol: str
    long_dex: str
    short_dex: str
    entry_spread: Optional[float]
    alert_threshold: float
    created_at: float

    # Current metrics
    long_rate_1h: Optional[float]
    short_rate_1h: Optional[float]
    current_spread: Optional[float]
    cashflow_per_10k: Optional[float]

    # Status
    status: Status
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "long_dex": self.long_dex,
            "short_dex": self.short_dex,
            "entry_spread": self.entry_spread,
            "alert_threshold": self.alert_threshold,
            "created_at": self.created_at,
            "long_rate_1h": self.long_rate_1h,
            "short_rate_1h": self.short_rate_1h,
            "current_spread": self.current_spread,
            "cashflow_per_10k": self.cashflow_per_10k,
            "status": self.status,
            "message": self.message,
        }


class PositionMonitor:
    """
    Monitor manually-entered positions against live funding rate data.

    This class does NOT connect to exchange private APIs.
    All positions are manually recorded by the user.
    Market data is fetched from the shared MarketDataStore.
    """

    DEFAULT_POSITIONS_FILE = "positions.json"

    def __init__(self, positions_file: Optional[str] = None) -> None:
        """
        Initialize the position monitor.

        Args:
            positions_file: Path to positions.json.
                           Defaults to arb_bot/positions.json.
        """
        if positions_file is None:
            module_dir = Path(__file__).parent.parent
            positions_file = str(module_dir / self.DEFAULT_POSITIONS_FILE)

        self.positions_file = Path(positions_file)
        self._positions: List[Position] = []

        # Load existing positions
        self._load_positions()

    def _load_positions(self) -> None:
        """Load positions from JSON file."""
        if not self.positions_file.exists():
            logger.info(
                "No positions file found at %s, starting with empty list",
                self.positions_file
            )
            self._positions = []
            return

        try:
            with open(self.positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._positions = [Position.from_dict(p) for p in data]
            logger.info(
                "Loaded %d positions from %s",
                len(self._positions),
                self.positions_file
            )
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in positions file: %s", e)
            self._positions = []
        except Exception as e:
            logger.error("Failed to load positions: %s", e)
            self._positions = []

    def _save_positions(self) -> None:
        """Save positions to JSON file."""
        try:
            # Ensure parent directory exists
            self.positions_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.positions_file, "w", encoding="utf-8") as f:
                json.dump(
                    [p.to_dict() for p in self._positions],
                    f,
                    indent=2
                )
            logger.debug(
                "Saved %d positions to %s",
                len(self._positions),
                self.positions_file
            )
        except Exception as e:
            logger.error("Failed to save positions: %s", e)
            raise

    def add_position(
        self,
        symbol: str,
        long_dex: str,
        short_dex: str,
        alert_threshold: float,
        entry_spread: Optional[float] = None,
    ) -> Position:
        """
        Add a new position to track.

        Args:
            symbol: Normalized symbol (e.g., "ETH", "BTC")
            long_dex: DEX name for long position (e.g., "Lighter")
            short_dex: DEX name for short position (e.g., "Paradex")
            alert_threshold: Spread threshold for WARNING alert (e.g., 0.0005)
            entry_spread: Optional spread at entry time

        Returns:
            The created Position object
        """
        position = Position(
            id=str(uuid.uuid4()),
            symbol=symbol.upper(),
            long_dex=long_dex,
            short_dex=short_dex,
            alert_threshold=alert_threshold,
            entry_spread=entry_spread,
            created_at=datetime.now().timestamp(),
        )

        self._positions.append(position)
        self._save_positions()

        logger.info(
            "Added position %s: %s Long=%s Short=%s threshold=%.4f%%",
            position.id[:8],
            position.symbol,
            position.long_dex,
            position.short_dex,
            position.alert_threshold * 100,
        )

        return position

    def remove_position(self, position_id: str) -> bool:
        """
        Remove a position by ID.

        Args:
            position_id: UUID of the position to remove

        Returns:
            True if position was found and removed, False otherwise
        """
        for i, p in enumerate(self._positions):
            if p.id == position_id:
                removed = self._positions.pop(i)
                self._save_positions()
                logger.info(
                    "Removed position %s: %s %s/%s",
                    removed.id[:8],
                    removed.symbol,
                    removed.long_dex,
                    removed.short_dex,
                )
                return True

        logger.warning("Position %s not found", position_id)
        return False

    def get_positions(self) -> List[Position]:
        """Get all tracked positions."""
        return list(self._positions)

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a single position by ID."""
        for p in self._positions:
            if p.id == position_id:
                return p
        return None

    def get_positions_status(
        self,
        store: MarketDataStore
    ) -> List[Dict[str, Any]]:
        """
        Check all positions against live market data.

        Args:
            store: MarketDataStore containing current funding rates

        Returns:
            List of dicts containing position info + current metrics + status.
            Each dict has:
                - id, symbol, long_dex, short_dex, entry_spread, alert_threshold, created_at
                - long_rate_1h, short_rate_1h, current_spread, cashflow_per_10k
                - status ("OK", "WARNING", "CRITICAL", "NO_DATA")
                - message (human-readable status description)
        """
        results: List[Dict[str, Any]] = []

        for position in self._positions:
            result = self._check_position(position, store)
            results.append(result.to_dict())

        return results

    def _check_position(
        self,
        position: Position,
        store: MarketDataStore
    ) -> PositionStatusResult:
        """
        Check a single position against live data.

        Strategy logic:
        - Long on long_dex: pay funding when rate > 0
        - Short on short_dex: receive funding when rate > 0
        - current_spread = short_rate - long_rate
        - Positive spread = profit (receive more than pay)
        - cashflow_per_10k = current_spread * 10000 ($/hour for $10k notional)

        Status determination:
        - CRITICAL: current_spread < 0 (Negative Carry - losing money)
        - WARNING: current_spread < alert_threshold (below acceptable level)
        - OK: current_spread >= alert_threshold
        - NO_DATA: missing market data for one or both DEXs
        """
        # Fetch rates from store
        long_entry = store.get_rate(position.symbol, position.long_dex)
        short_entry = store.get_rate(position.symbol, position.short_dex)

        # Handle missing data
        if long_entry is None or short_entry is None:
            missing = []
            if long_entry is None:
                missing.append(f"{position.long_dex} (long)")
            if short_entry is None:
                missing.append(f"{position.short_dex} (short)")

            return PositionStatusResult(
                id=position.id,
                symbol=position.symbol,
                long_dex=position.long_dex,
                short_dex=position.short_dex,
                entry_spread=position.entry_spread,
                alert_threshold=position.alert_threshold,
                created_at=position.created_at,
                long_rate_1h=None,
                short_rate_1h=None,
                current_spread=None,
                cashflow_per_10k=None,
                status="NO_DATA",
                message=f"Missing data: {', '.join(missing)}",
            )

        # Calculate current metrics
        long_rate_1h = long_entry.rate_1h
        short_rate_1h = short_entry.rate_1h
        current_spread = short_rate_1h - long_rate_1h
        cashflow_per_10k = current_spread * 10000

        # Determine status
        status: Status
        message: str

        if current_spread < 0:
            status = "CRITICAL"
            message = (
                f"NEGATIVE CARRY: Losing ${abs(cashflow_per_10k):.2f}/h per $10k. "
                f"Consider closing position."
            )
        elif current_spread < position.alert_threshold:
            status = "WARNING"
            message = (
                f"Spread {current_spread*100:.4f}% below threshold "
                f"{position.alert_threshold*100:.4f}%. "
                f"Cashflow: ${cashflow_per_10k:.2f}/h per $10k."
            )
        else:
            status = "OK"
            message = (
                f"Healthy. Spread {current_spread*100:.4f}% above threshold. "
                f"Cashflow: ${cashflow_per_10k:.2f}/h per $10k."
            )

        return PositionStatusResult(
            id=position.id,
            symbol=position.symbol,
            long_dex=position.long_dex,
            short_dex=position.short_dex,
            entry_spread=position.entry_spread,
            alert_threshold=position.alert_threshold,
            created_at=position.created_at,
            long_rate_1h=long_rate_1h,
            short_rate_1h=short_rate_1h,
            current_spread=current_spread,
            cashflow_per_10k=cashflow_per_10k,
            status=status,
            message=message,
        )

    def get_alerts(self, store: MarketDataStore) -> List[Dict[str, Any]]:
        """
        Get positions that need attention (not OK status).

        Args:
            store: MarketDataStore containing current funding rates

        Returns:
            List of position status dicts with status WARNING, CRITICAL, or NO_DATA
        """
        all_statuses = self.get_positions_status(store)
        return [s for s in all_statuses if s["status"] != "OK"]

    def reload(self) -> None:
        """Reload positions from file (useful if file was edited externally)."""
        self._load_positions()

    def update_position(
        self,
        position_id: str,
        alert_threshold: Optional[float] = None,
        entry_spread: Optional[float] = None,
    ) -> bool:
        """
        Update an existing position's parameters.

        Args:
            position_id: UUID of the position to update
            alert_threshold: New alert threshold (optional)
            entry_spread: New entry spread (optional)

        Returns:
            True if position was found and updated, False otherwise
        """
        for p in self._positions:
            if p.id == position_id:
                if alert_threshold is not None:
                    p.alert_threshold = alert_threshold
                if entry_spread is not None:
                    p.entry_spread = entry_spread
                self._save_positions()
                logger.info("Updated position %s", position_id[:8])
                return True

        logger.warning("Position %s not found for update", position_id)
        return False

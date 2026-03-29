"""
Hyperliquid read-only data client.

Wraps hyperliquid-python-sdk Info class with retry logic,
TTL caching, and structured dict responses.

No API key needed — all info endpoints are public.
"""

from __future__ import annotations

import time
import logging
from typing import Any

from hyperliquid.info import Info

logger = logging.getLogger(__name__)

# Perp assets for monitoring (BTC, ETH as correlation references, PAXG as gold-backed perp)
DEFAULT_PERP_ASSETS = ["BTC", "ETH", "PAXG", "SOL"]

# Spot commodity tokens on Hyperliquid (FLX/RWA tokens tracking real-world prices)
DEFAULT_SPOT_COMMODITIES = ["GLD", "SLV"]

# xyz: namespace — commodity/macro perps (separate from standard perp universe).
DEFAULT_XYZ_ASSETS = [
    "xyz:CL", "xyz:BRENTOIL", "xyz:GOLD", "xyz:SILVER",
    "xyz:COPPER", "xyz:NATGAS", "xyz:PLATINUM", "xyz:PALLADIUM",
    "xyz:EUR", "xyz:JPY",
]

# Combined default set for the monitor agent
DEFAULT_ASSETS = DEFAULT_PERP_ASSETS + DEFAULT_SPOT_COMMODITIES + DEFAULT_XYZ_ASSETS

# Retry config
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds

# Default cache TTL
DEFAULT_CACHE_TTL = 60  # seconds


class _Cache:
    """Simple TTL-based in-memory cache."""

    def __init__(self, ttl: int = DEFAULT_CACHE_TTL):
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.monotonic(), val)

    def clear(self) -> None:
        self._store.clear()


def _retry(fn, *args, **kwargs) -> Any:
    """Call fn with exponential backoff on network errors."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.1fs",
                attempt + 1, MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


class HyperliquidClient:
    """Read-only Hyperliquid data client for perps, spot, and xyz markets.

    Supports:
    - Perpetual futures (BTC, ETH, PAXG, etc.)
    - Spot commodity tokens (GLD, SLV — Hyperliquid FLX/RWA assets)
    - xyz: namespace commodity perps (xyz:CL — WTI crude oil, etc.)

    Args:
        cache_ttl: Cache lifetime in seconds. Set to 0 to disable caching.
        base_url: Override the Hyperliquid API base URL (for testnet).
    """

    def __init__(
        self,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        base_url: str | None = None,
    ):
        kwargs: dict[str, Any] = {"skip_ws": True}
        if base_url:
            kwargs["base_url"] = base_url
        self._info = Info(**kwargs)
        self._cache = _Cache(ttl=cache_ttl)

    def fetch_meta(self) -> dict:
        """Fetch exchange metadata (universe of assets, fee schedules)."""
        cached = self._cache.get("meta")
        if cached is not None:
            return cached
        result = _retry(self._info.meta)
        self._cache.set("meta", result)
        return result

    def list_assets(self) -> list[str]:
        """Return list of all available asset symbols."""
        meta = self.fetch_meta()
        return [a["name"] for a in meta["universe"]]

    def fetch_asset_contexts(
        self, assets: list[str] | None = None,
    ) -> list[dict]:
        """Fetch current context for assets (price, funding, OI, volume)."""
        cache_key = "asset_contexts"
        cached = self._cache.get(cache_key)
        if cached is None:
            meta = self.fetch_meta()
            raw_ctxs = _retry(self._info.meta_and_asset_ctxs)
            universe = meta["universe"]
            ctx_list = raw_ctxs[1] if isinstance(raw_ctxs, (list, tuple)) else []
            cached = []
            for asset_meta, ctx in zip(universe, ctx_list):
                cached.append({
                    "asset": asset_meta["name"],
                    "mark_price": ctx.get("markPx"),
                    "funding_rate": ctx.get("funding"),
                    "open_interest": ctx.get("openInterest"),
                    "day_volume": ctx.get("dayNtlVlm"),
                    "oracle_price": ctx.get("oraclePx"),
                    "premium": ctx.get("premium"),
                })
            self._cache.set(cache_key, cached)

        if assets:
            asset_set = {a.upper() for a in assets}
            return [c for c in cached if c["asset"] in asset_set]
        return cached

    def fetch_funding_history(
        self, asset: str, start_time: int | None = None, end_time: int | None = None,
    ) -> list[dict]:
        """Fetch historical funding rates for an asset."""
        now_ms = int(time.time() * 1000)
        if end_time is None:
            end_time = now_ms
        if start_time is None:
            start_time = end_time - 86_400_000  # 24h

        cache_key = f"funding:{asset}:{start_time}:{end_time}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if ":" in asset:
            payload = {"type": "fundingHistory", "coin": asset,
                       "startTime": start_time, "endTime": end_time}
            raw = _retry(self._post, payload)
        else:
            raw = _retry(self._info.funding_history, asset, start_time, end_time)
        result = []
        for entry in raw:
            result.append({
                "time": entry.get("time"),
                "asset": entry.get("coin", asset),
                "funding_rate": entry.get("fundingRate"),
                "premium": entry.get("premium"),
            })
        self._cache.set(cache_key, result)
        return result

    def fetch_orderbook_snapshot(self, asset: str, depth: int = 10) -> dict:
        """Fetch current orderbook snapshot for an asset."""
        cache_key = f"book:{asset}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if ":" in asset:
            raw = _retry(self._post, {"type": "l2Book", "coin": asset})
        else:
            raw = _retry(self._info.l2_snapshot, asset)
        levels = raw.get("levels", [[], []])
        bids = [{"price": l["px"], "size": l["sz"]} for l in levels[0][:depth]]
        asks = [{"price": l["px"], "size": l["sz"]} for l in levels[1][:depth]]
        result = {
            "asset": asset,
            "bids": bids,
            "asks": asks,
            "timestamp": int(time.time() * 1000),
        }
        self._cache.set(cache_key, result)
        return result

    def _post(self, payload: dict) -> Any:
        """Direct API call bypassing SDK's name_to_coin mapping."""
        return self._info.post("/info", payload)

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()

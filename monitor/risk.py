"""
Risk scoring engine for Hyperliquid perps monitoring.

Computes a composite risk score (0-100) from funding rate magnitude,
volume vs 24h average, OI change rate, and basis deviation from index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default weights sum to 1.0
DEFAULT_WEIGHTS = {
    "funding": 0.30,
    "volume_spike": 0.25,
    "oi_shift": 0.25,
    "basis": 0.20,
}

# Thresholds at which the component score hits 100.
# Separate defaults for crypto and commodity asset classes.
THRESHOLDS = {
    "crypto": {
        "funding_rate_extreme": 0.001,   # 0.1%/hr is extreme
        "volume_spike_ratio": 5.0,       # 5x average volume
        "oi_change_pct": 0.20,           # 20% OI change
        "basis_deviation_pct": 0.05,     # 5% basis deviation
    },
    "commodity": {
        "funding_rate_extreme": 0.0005,  # tighter for commodities
        "volume_spike_ratio": 3.0,
        "oi_change_pct": 0.15,
        "basis_deviation_pct": 0.03,
    },
}

# Assets classified as commodities; everything else is crypto
COMMODITY_ASSETS = {"PAXG", "GOLD", "SILVER", "OIL", "WTI"}

DEFAULT_ALERT_THRESHOLD = 60


@dataclass
class AssetRisk:
    """Risk assessment for a single asset."""

    asset: str
    funding_score: float
    volume_score: float
    oi_score: float
    basis_score: float
    composite: float
    alerts: list[str] = field(default_factory=list)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _asset_class(asset: str) -> str:
    return "commodity" if asset.upper() in COMMODITY_ASSETS else "crypto"


def score_funding(funding_rate: float, threshold: float) -> float:
    """Score funding rate magnitude 0-100."""
    if threshold <= 0:
        return 0.0
    return _clamp(abs(funding_rate) / threshold * 100)


def score_volume_spike(current_volume: float, avg_volume: float, threshold: float) -> float:
    """Score volume spike relative to average."""
    if avg_volume <= 0 or threshold <= 0:
        return 0.0
    ratio = current_volume / avg_volume
    return _clamp(ratio / threshold * 100)


def score_oi_shift(current_oi: float, previous_oi: float, threshold: float) -> float:
    """Score OI change as percentage of previous."""
    if previous_oi <= 0 or threshold <= 0:
        return 0.0
    change_pct = abs(current_oi - previous_oi) / previous_oi
    return _clamp(change_pct / threshold * 100)


def score_basis(mark_price: float, oracle_price: float, threshold: float) -> float:
    """Score basis deviation between mark and oracle price."""
    if oracle_price <= 0 or threshold <= 0:
        return 0.0
    deviation = abs(mark_price - oracle_price) / oracle_price
    return _clamp(deviation / threshold * 100)


def compute_asset_risk(
    asset: str,
    funding_rate: float,
    current_volume: float,
    avg_volume: float,
    current_oi: float,
    previous_oi: float,
    mark_price: float,
    oracle_price: float,
    weights: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
    alert_threshold: float = DEFAULT_ALERT_THRESHOLD,
) -> AssetRisk:
    """Compute composite risk score for a single asset.

    Args:
        asset: Asset symbol (e.g. "BTC").
        funding_rate: Current hourly funding rate.
        current_volume: Current 24h volume.
        avg_volume: Average 24h volume (historical baseline).
        current_oi: Current open interest.
        previous_oi: Previous open interest snapshot.
        mark_price: Current mark price.
        oracle_price: Current oracle/index price.
        weights: Override component weights.
        thresholds: Override per-component thresholds.
        alert_threshold: Composite score threshold to generate alerts.

    Returns:
        AssetRisk with component scores and composite.
    """
    w = weights or DEFAULT_WEIGHTS
    asset_cls = _asset_class(asset)
    t = thresholds or THRESHOLDS[asset_cls]

    fs = score_funding(funding_rate, t["funding_rate_extreme"])
    vs = score_volume_spike(current_volume, avg_volume, t["volume_spike_ratio"])
    ois = score_oi_shift(current_oi, previous_oi, t["oi_change_pct"])
    bs = score_basis(mark_price, oracle_price, t["basis_deviation_pct"])

    composite = _clamp(
        fs * w["funding"] + vs * w["volume_spike"] + ois * w["oi_shift"] + bs * w["basis"]
    )

    alerts: list[str] = []
    if composite >= alert_threshold:
        alerts.append(
            f"[{asset}] HIGH RISK ({composite:.1f}/100): "
            f"funding={fs:.0f}, vol_spike={vs:.0f}, oi_shift={ois:.0f}, basis={bs:.0f}"
        )
    if fs >= 80:
        direction = "positive" if funding_rate > 0 else "negative"
        alerts.append(f"[{asset}] Extreme funding rate ({direction}): {funding_rate:.6f}")
    if vs >= 80:
        alerts.append(f"[{asset}] Volume spike: {current_volume:.0f} vs avg {avg_volume:.0f}")
    if ois >= 80:
        pct = abs(current_oi - previous_oi) / previous_oi * 100 if previous_oi > 0 else 0
        alerts.append(f"[{asset}] OI shift: {pct:.1f}% change")
    if bs >= 80:
        alerts.append(
            f"[{asset}] Basis deviation: mark={mark_price:.2f} vs oracle={oracle_price:.2f}"
        )

    return AssetRisk(
        asset=asset,
        funding_score=fs,
        volume_score=vs,
        oi_score=ois,
        basis_score=bs,
        composite=composite,
        alerts=alerts,
    )


def compute_portfolio_risk(
    asset_risks: list[AssetRisk],
) -> tuple[float, list[str]]:
    """Compute aggregate portfolio risk from individual asset risks.

    Returns:
        (average composite score, combined alert list)
    """
    if not asset_risks:
        return 0.0, []
    avg = sum(r.composite for r in asset_risks) / len(asset_risks)
    all_alerts = []
    for r in asset_risks:
        all_alerts.extend(r.alerts)
    return avg, all_alerts

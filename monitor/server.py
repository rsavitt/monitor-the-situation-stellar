"""Hyperliquid market data API server.

The upstream risk engine that the Stellar x402 gateway proxies to.

Run:
    uvicorn monitor.server:app --host 0.0.0.0 --port 8402
    # or
    python -m monitor.server

Environment variables:
    HL_CACHE_TTL  — Hyperliquid data cache TTL in seconds (default: 60)
    HOST          — Listen host (default: 0.0.0.0)
    PORT          — Listen port (default: 8402)
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from monitor.client import HyperliquidClient, DEFAULT_PERP_ASSETS
from monitor.risk import compute_asset_risk
from monitor.historical import fetch_candles, fetch_all_funding, analyze_candles, detect_events, analyze_funding

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Monitor the Situation — Risk Engine",
    description="Hyperliquid perps risk scores, alerts, and historical analysis.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# --- State ---

_hl_client: HyperliquidClient | None = None
_volume_history: dict[str, list[float]] = {}
_previous_oi: dict[str, float] = {}
_HISTORY_WINDOW = 12

# --- Pricing (for reference in responses) ---
PRICE_RISK_SCORES = Decimal("0.01")
PRICE_SINGLE_ASSET = Decimal("0.005")
PRICE_ALERTS = Decimal("0.005")
PRICE_PRICES = Decimal("0.002")
PRICE_HISTORICAL_BASE = Decimal("0.05")
PRICE_HISTORICAL_PER_DAY = Decimal("0.003")


@app.on_event("startup")
async def startup():
    global _hl_client
    cache_ttl = int(os.environ.get("HL_CACHE_TTL", "60"))
    _hl_client = HyperliquidClient(cache_ttl=cache_ttl)
    logger.info("Risk engine started (cache_ttl=%ds)", cache_ttl)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "risk-engine"}


def _get_client() -> HyperliquidClient:
    if _hl_client is None:
        raise HTTPException(503, detail="Risk engine not initialized")
    return _hl_client


def _build_asset_snapshot(ctx: dict) -> dict:
    mark = float(ctx.get("mark_price") or 0)
    oracle = float(ctx.get("oracle_price") or 0)
    funding = float(ctx.get("funding_rate") or 0)
    oi = float(ctx.get("open_interest") or 0)
    volume = float(ctx.get("day_volume") or 0)
    asset = ctx["asset"]

    _volume_history.setdefault(asset, []).append(volume)
    if len(_volume_history[asset]) > _HISTORY_WINDOW:
        _volume_history[asset] = _volume_history[asset][-_HISTORY_WINDOW:]

    avg_vol = sum(_volume_history[asset]) / len(_volume_history[asset])
    prev_oi = _previous_oi.get(asset, oi)
    _previous_oi[asset] = oi

    return {
        "asset": asset,
        "mark_price": mark,
        "oracle_price": oracle,
        "funding_rate": funding,
        "open_interest": oi,
        "day_volume": volume,
        "avg_volume": round(avg_vol, 2),
        "previous_oi": prev_oi,
    }


def _score_asset(snapshot: dict) -> dict:
    risk = compute_asset_risk(
        asset=snapshot["asset"],
        funding_rate=snapshot["funding_rate"],
        current_volume=snapshot["day_volume"],
        avg_volume=snapshot["avg_volume"],
        current_oi=snapshot["open_interest"],
        previous_oi=snapshot["previous_oi"],
        mark_price=snapshot["mark_price"],
        oracle_price=snapshot["oracle_price"],
    )
    return {
        "asset": risk.asset,
        "mark_price": snapshot["mark_price"],
        "oracle_price": snapshot["oracle_price"],
        "funding_rate": snapshot["funding_rate"],
        "open_interest": snapshot["open_interest"],
        "day_volume": snapshot["day_volume"],
        "avg_volume": snapshot["avg_volume"],
        "risk_score": round(risk.composite, 1),
        "component_scores": {
            "funding": round(risk.funding_score, 1),
            "volume_spike": round(risk.volume_score, 1),
            "oi_shift": round(risk.oi_score, 1),
            "basis": round(risk.basis_score, 1),
        },
        "alerts": risk.alerts,
    }


# --- Endpoints ---

@app.get("/api/v1/market/risk-scores")
async def get_risk_scores(request: Request, assets: str | None = None):
    """All assets with composite risk scores."""
    client = _get_client()
    asset_list = [a.strip() for a in assets.split(",")] if assets else DEFAULT_PERP_ASSETS
    contexts = client.fetch_asset_contexts(asset_list)
    scored = [_score_asset(_build_asset_snapshot(ctx)) for ctx in contexts]

    portfolio_risk = (
        sum(s["risk_score"] for s in scored) / len(scored) if scored else 0.0
    )
    all_alerts = [a for s in scored for a in s["alerts"]]

    return {
        "timestamp": time.time(),
        "assets": scored,
        "portfolio_risk_score": round(portfolio_risk, 1),
        "alerts": all_alerts,
        "pricing": {"amount_usdc": str(PRICE_RISK_SCORES), "network": "stellar"},
    }


@app.get("/api/v1/market/risk-scores/{asset}")
async def get_asset_risk(request: Request, asset: str):
    """Deep dive risk score for a single asset."""
    client = _get_client()
    contexts = client.fetch_asset_contexts([asset.upper()])
    if not contexts:
        raise HTTPException(404, detail=f"Asset '{asset}' not found")

    snapshot = _build_asset_snapshot(contexts[0])
    scored = _score_asset(snapshot)
    funding_history = client.fetch_funding_history(asset.upper())
    scored["funding_history_24h"] = funding_history[-24:] if funding_history else []
    book = client.fetch_orderbook_snapshot(asset.upper(), depth=5)
    scored["orderbook"] = book

    return {
        "timestamp": time.time(),
        **scored,
        "pricing": {"amount_usdc": str(PRICE_SINGLE_ASSET), "network": "stellar"},
    }


@app.get("/api/v1/market/alerts")
async def get_alerts(request: Request, threshold: float = 60.0):
    """Active alerts only — assets with risk score >= threshold."""
    client = _get_client()
    contexts = client.fetch_asset_contexts(DEFAULT_PERP_ASSETS)
    scored = [_score_asset(_build_asset_snapshot(ctx)) for ctx in contexts]
    alerting = [s for s in scored if s["risk_score"] >= threshold]

    return {
        "timestamp": time.time(),
        "threshold": threshold,
        "alerting_assets": alerting,
        "total_monitored": len(scored),
        "pricing": {"amount_usdc": str(PRICE_ALERTS), "network": "stellar"},
    }


@app.get("/api/v1/market/prices")
async def get_prices(request: Request, assets: str | None = None):
    """Raw price snapshot — no risk scoring."""
    client = _get_client()
    asset_list = [a.strip() for a in assets.split(",")] if assets else DEFAULT_PERP_ASSETS
    contexts = client.fetch_asset_contexts(asset_list)
    prices = []
    for ctx in contexts:
        prices.append({
            "asset": ctx["asset"],
            "mark_price": float(ctx.get("mark_price") or 0),
            "oracle_price": float(ctx.get("oracle_price") or 0),
            "funding_rate": float(ctx.get("funding_rate") or 0),
            "open_interest": float(ctx.get("open_interest") or 0),
            "day_volume": float(ctx.get("day_volume") or 0),
        })

    return {
        "timestamp": time.time(),
        "assets": prices,
        "pricing": {"amount_usdc": str(PRICE_PRICES), "network": "stellar"},
    }


@app.get("/api/v1/market/historical")
async def get_historical(
    request: Request,
    assets: str | None = None,
    days: int = 30,
):
    """Historical analysis with candle data, funding stats, and notable events."""
    days = max(1, min(60, days))
    price = PRICE_HISTORICAL_BASE + PRICE_HISTORICAL_PER_DAY * days
    client = _get_client()
    asset_list = [a.strip() for a in assets.split(",")] if assets else DEFAULT_PERP_ASSETS

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 86_400_000)

    asset_data = {}
    for asset_name in asset_list:
        candles = fetch_candles(client._info, asset_name, "1d", start_ms, now_ms)
        candle_days = analyze_candles(candles)
        candle_events = detect_events(candle_days, asset_name) if candle_days else []
        funding = fetch_all_funding(client._info, asset_name, start_ms, now_ms)
        funding_stats = analyze_funding(funding, asset_name)

        asset_data[asset_name] = {
            "candle_days": candle_days,
            "candle_events": candle_events,
            "funding_stats": funding_stats,
        }

    return {
        "timestamp": time.time(),
        "lookback_days": days,
        "assets": list(asset_data.keys()),
        "asset_data": asset_data,
        "pricing": {"amount_usdc": str(price), "network": "stellar"},
    }


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8402"))
    uvicorn.run("monitor.server:app", host=host, port=port, reload=False)

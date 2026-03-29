#!/usr/bin/env python3
"""
Hyperliquid 2-month historical analysis.

Fetches daily candles + funding history for key assets, computes daily risk
scores, and identifies notable events (volume spikes, large moves, extreme funding).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from hyperliquid.info import Info

# Assets to analyze
PERP_ASSETS = ["BTC", "ETH", "SOL", "PAXG"]
# We'll try xyz: assets but they may not have historical candles
XYZ_ASSETS = ["xyz:GOLD", "xyz:SILVER", "xyz:CL"]

LOOKBACK_DAYS = 60


def _post(info: Info, payload: dict):
    return info.post("/info", payload)


def fetch_candles(info: Info, coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch candle data. Works for both standard and xyz: assets."""
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    try:
        raw = _post(info, payload)
        return raw if isinstance(raw, list) else []
    except Exception as e:
        print(f"  Warning: candle fetch failed for {coin}: {e}")
        return []


def fetch_all_funding(info: Info, coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """Paginate through all funding history for an asset."""
    all_entries = []
    cursor = start_ms
    while cursor < end_ms:
        try:
            if ":" in coin:
                raw = _post(info, {
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": cursor,
                    "endTime": end_ms,
                })
            else:
                raw = info.funding_history(coin, cursor, end_ms)
        except Exception as e:
            print(f"  Warning: funding fetch failed for {coin} at {cursor}: {e}")
            break

        if not raw:
            break
        all_entries.extend(raw)
        last_time = raw[-1].get("time", cursor)
        if last_time <= cursor:
            break
        cursor = last_time + 1
        time.sleep(0.1)  # rate limit courtesy

    return all_entries


def analyze_candles(candles: list[dict]) -> list[dict]:
    """Convert raw candles to daily summaries with computed metrics."""
    days = []
    for c in candles:
        t = c.get("t", c.get("time", 0))
        o = float(c.get("o", c.get("open", 0)))
        h = float(c.get("h", c.get("high", 0)))
        l = float(c.get("l", c.get("low", 0)))
        close = float(c.get("c", c.get("close", 0)))
        vol = float(c.get("v", c.get("vlm", c.get("volume", 0))))

        if o == 0:
            continue

        daily_return = (close - o) / o * 100
        daily_range = (h - l) / o * 100 if o > 0 else 0

        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        days.append({
            "date": dt.strftime("%Y-%m-%d"),
            "open": o,
            "high": h,
            "low": l,
            "close": close,
            "volume": vol,
            "daily_return_pct": round(daily_return, 2),
            "daily_range_pct": round(daily_range, 2),
        })
    return days


def detect_events(days: list[dict], asset: str) -> list[dict]:
    """Find notable events: volume spikes, large moves."""
    if len(days) < 5:
        return []

    events = []
    volumes = [d["volume"] for d in days]

    for i, d in enumerate(days):
        # Volume spike: >3x the 7-day trailing average
        lookback = volumes[max(0, i - 7):i] if i > 0 else [d["volume"]]
        avg_vol = sum(lookback) / len(lookback) if lookback else d["volume"]
        vol_ratio = d["volume"] / avg_vol if avg_vol > 0 else 0

        if vol_ratio > 3.0:
            events.append({
                "date": d["date"],
                "asset": asset,
                "type": "volume_spike",
                "detail": f"{vol_ratio:.1f}x avg volume (${d['volume']:,.0f} vs avg ${avg_vol:,.0f})",
                "severity": "high" if vol_ratio > 5.0 else "medium",
            })

        # Large daily move: >5%
        if abs(d["daily_return_pct"]) > 5.0:
            direction = "up" if d["daily_return_pct"] > 0 else "down"
            events.append({
                "date": d["date"],
                "asset": asset,
                "type": "large_move",
                "detail": f"{d['daily_return_pct']:+.1f}% ({direction})",
                "severity": "high" if abs(d["daily_return_pct"]) > 10.0 else "medium",
            })

        # Wide range day: >8%
        if d["daily_range_pct"] > 8.0:
            events.append({
                "date": d["date"],
                "asset": asset,
                "type": "high_volatility",
                "detail": f"{d['daily_range_pct']:.1f}% intraday range",
                "severity": "medium",
            })

    return events


def analyze_funding(funding: list[dict], asset: str) -> dict:
    """Compute funding statistics and detect extreme periods."""
    if not funding:
        return {"asset": asset, "entries": 0, "events": []}

    rates = [float(f.get("fundingRate", 0)) for f in funding]
    avg_rate = sum(rates) / len(rates)
    max_rate = max(rates)
    min_rate = min(rates)

    # Find streaks of consistently positive/negative funding
    events = []
    streak_dir = None
    streak_start = None
    streak_count = 0

    for f in funding:
        rate = float(f.get("fundingRate", 0))
        t = f.get("time", 0)
        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc) if t else None

        current_dir = "positive" if rate > 0.0001 else ("negative" if rate < -0.0001 else "neutral")

        if current_dir == streak_dir and current_dir != "neutral":
            streak_count += 1
        else:
            if streak_count >= 48 and streak_dir:  # 48 hours = 2 days
                events.append({
                    "date": streak_start.strftime("%Y-%m-%d") if streak_start else "?",
                    "asset": asset,
                    "type": "funding_streak",
                    "detail": f"{streak_count}h consecutive {streak_dir} funding",
                    "severity": "high" if streak_count >= 72 else "medium",
                })
            streak_dir = current_dir
            streak_start = dt
            streak_count = 1

        # Extreme single-hour funding
        if abs(rate) > 0.0005:
            events.append({
                "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "?",
                "asset": asset,
                "type": "extreme_funding",
                "detail": f"Funding rate: {rate:.6f} ({rate*100:.4f}%/hr)",
                "severity": "high" if abs(rate) > 0.001 else "medium",
            })

    return {
        "asset": asset,
        "entries": len(rates),
        "avg_rate": round(avg_rate, 8),
        "max_rate": round(max_rate, 8),
        "min_rate": round(min_rate, 8),
        "annualized_pct": round(avg_rate * 8760 * 100, 2),  # hourly * 8760 hours/year
        "events": events,
    }


def format_report(results: dict) -> str:
    """Format the full analysis as markdown."""
    lines = [
        "# Hyperliquid 2-Month Historical Analysis",
        f"**Period:** {results['start_date']} to {results['end_date']}",
        f"**Assets analyzed:** {', '.join(results['assets_analyzed'])}",
        "",
    ]

    # Summary table
    lines.append("## Price Performance Summary")
    lines.append("")
    lines.append("| Asset | Start | End | Return | Max Drawdown | Avg Daily Vol |")
    lines.append("|-------|-------|-----|--------|--------------|---------------|")

    for asset, data in results["asset_data"].items():
        days = data.get("candle_days", [])
        if not days:
            lines.append(f"| {asset} | N/A | N/A | N/A | N/A | N/A |")
            continue

        start_price = days[0]["close"]
        end_price = days[-1]["close"]
        total_return = (end_price - start_price) / start_price * 100
        avg_vol = sum(d["volume"] for d in days) / len(days)

        # Max drawdown
        peak = days[0]["close"]
        max_dd = 0
        for d in days:
            if d["close"] > peak:
                peak = d["close"]
            dd = (peak - d["close"]) / peak * 100
            if dd > max_dd:
                max_dd = dd

        lines.append(
            f"| {asset} "
            f"| ${start_price:,.2f} "
            f"| ${end_price:,.2f} "
            f"| {total_return:+.1f}% "
            f"| -{max_dd:.1f}% "
            f"| ${avg_vol:,.0f} |"
        )

    lines.append("")

    # Funding summary
    lines.append("## Funding Rate Summary")
    lines.append("")
    lines.append("| Asset | Avg Rate/hr | Annualized | Max | Min | Data Points |")
    lines.append("|-------|-------------|------------|-----|-----|-------------|")

    for asset, data in results["asset_data"].items():
        fs = data.get("funding_stats", {})
        if not fs or fs.get("entries", 0) == 0:
            lines.append(f"| {asset} | N/A | N/A | N/A | N/A | 0 |")
            continue
        lines.append(
            f"| {asset} "
            f"| {fs['avg_rate']:.6f} "
            f"| {fs['annualized_pct']:+.1f}% "
            f"| {fs['max_rate']:.6f} "
            f"| {fs['min_rate']:.6f} "
            f"| {fs['entries']} |"
        )

    lines.append("")

    # Notable events
    all_events = []
    for asset, data in results["asset_data"].items():
        all_events.extend(data.get("candle_events", []))
        all_events.extend(data.get("funding_stats", {}).get("events", []))

    if all_events:
        # Sort by date, high severity first
        all_events.sort(key=lambda e: (e["date"], 0 if e["severity"] == "high" else 1))

        high_events = [e for e in all_events if e["severity"] == "high"]
        lines.append(f"## Notable Events ({len(all_events)} total, {len(high_events)} high severity)")
        lines.append("")

        # Show high-severity events
        if high_events:
            lines.append("### High Severity")
            lines.append("")
            lines.append("| Date | Asset | Type | Detail |")
            lines.append("|------|-------|------|--------|")
            for e in high_events[:50]:  # cap at 50
                lines.append(f"| {e['date']} | {e['asset']} | {e['type']} | {e['detail']} |")
            lines.append("")

        # Medium severity summary
        med_events = [e for e in all_events if e["severity"] == "medium"]
        if med_events:
            lines.append(f"### Medium Severity ({len(med_events)} events)")
            lines.append("")
            # Group by type
            by_type: dict[str, int] = {}
            for e in med_events:
                by_type[e["type"]] = by_type.get(e["type"], 0) + 1
            for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
                lines.append(f"- **{t}**: {count} occurrences")
            lines.append("")

    # Correlation notes
    lines.append("## Cross-Asset Observations")
    lines.append("")

    btc_days = results["asset_data"].get("BTC", {}).get("candle_days", [])
    paxg_days = results["asset_data"].get("PAXG", {}).get("candle_days", [])

    if btc_days and paxg_days and len(btc_days) == len(paxg_days):
        # Simple correlation of daily returns
        btc_returns = [d["daily_return_pct"] for d in btc_days]
        paxg_returns = [d["daily_return_pct"] for d in paxg_days]
        n = len(btc_returns)
        if n > 5:
            mean_b = sum(btc_returns) / n
            mean_p = sum(paxg_returns) / n
            cov = sum((b - mean_b) * (p - mean_p) for b, p in zip(btc_returns, paxg_returns)) / n
            std_b = (sum((b - mean_b) ** 2 for b in btc_returns) / n) ** 0.5
            std_p = (sum((p - mean_p) ** 2 for p in paxg_returns) / n) ** 0.5
            corr = cov / (std_b * std_p) if std_b > 0 and std_p > 0 else 0
            lines.append(f"- **BTC/PAXG daily return correlation:** {corr:.3f}")
            if corr < -0.2:
                lines.append("  - Negative correlation suggests gold-backed assets acting as hedge")
            elif corr > 0.5:
                lines.append("  - High positive correlation — risk-on/risk-off trading in sync")
            else:
                lines.append("  - Low correlation — largely independent price action")

    lines.append("")
    return "\n".join(lines)


def main():
    info = Info(skip_ws=True)

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_dt = now - timedelta(days=LOOKBACK_DAYS)
    start_ms = int(start_dt.timestamp() * 1000)

    results = {
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": now.strftime("%Y-%m-%d"),
        "assets_analyzed": [],
        "asset_data": {},
    }

    all_assets = PERP_ASSETS + XYZ_ASSETS

    for asset in all_assets:
        print(f"\n--- Analyzing {asset} ---")

        # Fetch daily candles
        print(f"  Fetching daily candles...")
        candles = fetch_candles(info, asset, "1d", start_ms, end_ms)
        days = analyze_candles(candles)
        print(f"  Got {len(days)} daily candles")

        # Fetch funding history
        print(f"  Fetching funding history...")
        funding = fetch_all_funding(info, asset, start_ms, end_ms)
        print(f"  Got {len(funding)} funding entries")

        # Analyze
        candle_events = detect_events(days, asset) if days else []
        funding_stats = analyze_funding(funding, asset)

        results["assets_analyzed"].append(asset)
        results["asset_data"][asset] = {
            "candle_days": days,
            "candle_events": candle_events,
            "funding_stats": funding_stats,
        }

        time.sleep(0.2)  # rate limit courtesy

    # Generate report
    report_md = format_report(results)
    print("\n" + "=" * 80)
    print(report_md)

    # Save report
    report_path = os.path.join(os.path.dirname(__file__), "historical_report.md")
    with open(report_path, "w") as f:
        f.write(report_md)
    print(f"\nReport saved to {report_path}")

    return report_md


if __name__ == "__main__":
    main()

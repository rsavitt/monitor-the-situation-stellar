/**
 * x402 route configuration — maps each API endpoint to its Stellar payment requirement.
 *
 * Pricing tiers match the existing Base/USDC endpoints:
 *   - Risk scores (all assets): $0.01
 *   - Single asset deep dive:   $0.005
 *   - Active alerts:            $0.005
 *   - Raw prices:               $0.002
 *   - Historical analysis:      $0.05
 */

import type { Network } from "@x402/core/types";
import type { RoutesConfig } from "@x402/core/server";

export function buildRouteConfig(payTo: string, network: Network): RoutesConfig {
  return {
    "GET /api/v1/market/risk-scores": {
      accepts: [
        {
          scheme: "exact" as const,
          price: "$0.01",
          network,
          payTo,
        },
      ],
      description:
        "Composite risk scores for all monitored assets (BTC, ETH, PAXG, SOL, commodities)",
      mimeType: "application/json",
    },
    "GET /api/v1/market/risk-scores/:asset": {
      accepts: [
        {
          scheme: "exact" as const,
          price: "$0.005",
          network,
          payTo,
        },
      ],
      description:
        "Deep-dive risk analysis for a single asset with orderbook and funding history",
      mimeType: "application/json",
    },
    "GET /api/v1/market/alerts": {
      accepts: [
        {
          scheme: "exact" as const,
          price: "$0.005",
          network,
          payTo,
        },
      ],
      description: "Active high-risk alerts above configurable threshold",
      mimeType: "application/json",
    },
    "GET /api/v1/market/prices": {
      accepts: [
        {
          scheme: "exact" as const,
          price: "$0.002",
          network,
          payTo,
        },
      ],
      description: "Raw price snapshot (mark, oracle, funding) — no risk engine",
      mimeType: "application/json",
    },
    "GET /api/v1/market/historical": {
      accepts: [
        {
          scheme: "exact" as const,
          price: "$0.05",
          network,
          payTo,
        },
      ],
      description:
        "Historical analysis with candle data and funding statistics (1-60 day lookback)",
      mimeType: "application/json",
    },
  };
}

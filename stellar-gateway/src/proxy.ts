import type { Request, Response } from "express";
import type { GovernanceState } from "./governance.js";
import { recordUpstreamFailure } from "./governance.js";

/**
 * Creates a proxy handler that forwards requests to the upstream Python risk engine.
 * Strips x402-specific headers and forwards query params + path as-is.
 * Reports failures to the governance circuit breaker.
 */
export function createProxyHandler(upstreamUrl: string, governance?: GovernanceState) {
  return async (req: Request, res: Response) => {
    const target = new URL(req.originalUrl, upstreamUrl);

    try {
      const upstream = await fetch(target.toString(), {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
      });

      const body = await upstream.text();
      const contentType = upstream.headers.get("content-type");

      // Forward status and content-type
      res.status(upstream.status);
      if (contentType) {
        res.setHeader("Content-Type", contentType);
      }

      // Inject Stellar payment network info into JSON responses
      if (contentType?.includes("application/json")) {
        try {
          const data = JSON.parse(body);
          data.payment_network = "stellar";
          data.gateway = "stellar-x402";
          res.json(data);
          return;
        } catch {
          // If JSON parse fails, send raw body
        }
      }

      res.send(body);
    } catch (err) {
      if (governance) recordUpstreamFailure(governance);
      const message =
        err instanceof Error ? err.message : "Upstream request failed";
      res.status(502).json({
        error: "upstream_error",
        message,
        upstream: upstreamUrl,
      });
    }
  };
}

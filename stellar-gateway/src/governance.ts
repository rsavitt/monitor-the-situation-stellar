/**
 * Standalone governance middleware for the Stellar x402 gateway.
 *
 * Extracted from the Agency-OS governance module. Provides:
 * - Circuit breaker: pauses proxy after consecutive upstream failures
 * - Budget tracking: daily revenue cap (optional safety limit)
 * - Rate limiting: per-IP request throttling
 */

import type { Request, Response, NextFunction } from "express";

// ---------------------------------------------------------------------------
// Circuit Breaker
// ---------------------------------------------------------------------------

interface CircuitBreakerConfig {
  threshold: number; // consecutive failures before tripping
  cooldownMs: number; // how long to stay open
}

class CircuitBreaker {
  private failures = 0;
  private tripped = false;
  private trippedAt = 0;

  constructor(private config: CircuitBreakerConfig) {}

  recordFailure(): void {
    this.failures++;
    if (this.failures >= this.config.threshold && !this.tripped) {
      this.tripped = true;
      this.trippedAt = Date.now();
      console.warn(
        `[governance] Circuit breaker TRIPPED after ${this.failures} failures. ` +
          `Cooldown: ${this.config.cooldownMs / 1000}s`
      );
    }
  }

  recordSuccess(): void {
    if (this.tripped) {
      console.info("[governance] Circuit breaker RESET after successful request");
    }
    this.failures = 0;
    this.tripped = false;
    this.trippedAt = 0;
  }

  isOpen(): boolean {
    if (!this.tripped) return false;
    if (Date.now() - this.trippedAt >= this.config.cooldownMs) {
      return false; // half-open: allow retry
    }
    return true;
  }

  status() {
    return {
      failures: this.failures,
      tripped: this.tripped,
      trippedAt: this.trippedAt ? new Date(this.trippedAt).toISOString() : null,
    };
  }
}

// ---------------------------------------------------------------------------
// Budget Tracker
// ---------------------------------------------------------------------------

interface BudgetConfig {
  dailyLimitUsd: number;
}

class BudgetTracker {
  private spentUsd = 0;
  private dayStart = Date.now();

  constructor(private config: BudgetConfig) {}

  recordRevenue(amountUsd: number): void {
    this.maybeResetDay();
    this.spentUsd += amountUsd;
  }

  isExhausted(): boolean {
    this.maybeResetDay();
    return this.spentUsd >= this.config.dailyLimitUsd;
  }

  status() {
    this.maybeResetDay();
    return {
      dailyLimitUsd: this.config.dailyLimitUsd,
      spentUsd: this.spentUsd,
      remainingUsd: Math.max(0, this.config.dailyLimitUsd - this.spentUsd),
    };
  }

  private maybeResetDay(): void {
    if (Date.now() - this.dayStart >= 86_400_000) {
      this.spentUsd = 0;
      this.dayStart = Date.now();
    }
  }
}

// ---------------------------------------------------------------------------
// Rate Limiter (simple sliding window per IP)
// ---------------------------------------------------------------------------

interface RateLimitConfig {
  windowMs: number;
  maxRequests: number;
}

class RateLimiter {
  private windows = new Map<string, number[]>();

  constructor(private config: RateLimitConfig) {}

  isAllowed(ip: string): boolean {
    const now = Date.now();
    const cutoff = now - this.config.windowMs;
    let timestamps = this.windows.get(ip) || [];
    timestamps = timestamps.filter((t) => t > cutoff);
    if (timestamps.length >= this.config.maxRequests) {
      this.windows.set(ip, timestamps);
      return false;
    }
    timestamps.push(now);
    this.windows.set(ip, timestamps);
    return true;
  }
}

// ---------------------------------------------------------------------------
// Governance Config + Middleware
// ---------------------------------------------------------------------------

export interface GovernanceConfig {
  circuitBreaker?: {
    threshold?: number;
    cooldownMs?: number;
  };
  budget?: {
    dailyLimitUsd?: number;
  };
  rateLimit?: {
    windowMs?: number;
    maxRequests?: number;
  };
}

export interface GovernanceState {
  circuitBreaker: CircuitBreaker;
  budget: BudgetTracker;
  rateLimiter: RateLimiter;
}

// Pricing lookup by route pattern
const ROUTE_PRICES: Record<string, number> = {
  "/api/v1/market/risk-scores": 0.01,
  "/api/v1/market/alerts": 0.005,
  "/api/v1/market/prices": 0.002,
  "/api/v1/market/historical": 0.05,
};

function getPriceForPath(path: string): number {
  // Check exact match first
  if (ROUTE_PRICES[path]) return ROUTE_PRICES[path];
  // Check if it matches risk-scores/:asset pattern
  if (path.startsWith("/api/v1/market/risk-scores/")) return 0.005;
  return 0;
}

export function createGovernance(config: GovernanceConfig = {}): GovernanceState {
  return {
    circuitBreaker: new CircuitBreaker({
      threshold: config.circuitBreaker?.threshold ?? 3,
      cooldownMs: config.circuitBreaker?.cooldownMs ?? 600_000,
    }),
    budget: new BudgetTracker({
      dailyLimitUsd: config.budget?.dailyLimitUsd ?? 50,
    }),
    rateLimiter: new RateLimiter({
      windowMs: config.rateLimit?.windowMs ?? 60_000,
      maxRequests: config.rateLimit?.maxRequests ?? 60,
    }),
  };
}

/**
 * Express middleware that enforces governance policies before proxying.
 */
export function governanceMiddleware(state: GovernanceState) {
  return (req: Request, res: Response, next: NextFunction) => {
    // Circuit breaker check
    if (state.circuitBreaker.isOpen()) {
      res.status(503).json({
        error: "circuit_breaker_open",
        message: "Upstream service temporarily unavailable. Try again later.",
        retry_after_seconds: 60,
      });
      return;
    }

    // Budget check
    if (state.budget.isExhausted()) {
      res.status(503).json({
        error: "budget_exhausted",
        message: "Daily request budget exhausted. Resets in 24 hours.",
      });
      return;
    }

    // Rate limit check
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    if (!state.rateLimiter.isAllowed(ip)) {
      res.status(429).json({
        error: "rate_limited",
        message: "Too many requests. Please slow down.",
      });
      return;
    }

    // Track revenue on successful response
    const originalJson = res.json.bind(res);
    res.json = function (body: unknown) {
      if (res.statusCode >= 200 && res.statusCode < 300) {
        const price = getPriceForPath(req.path);
        if (price > 0) {
          state.budget.recordRevenue(price);
        }
        state.circuitBreaker.recordSuccess();
      }
      return originalJson(body);
    };

    next();
  };
}

/**
 * Call this when a proxy request to upstream fails.
 */
export function recordUpstreamFailure(state: GovernanceState): void {
  state.circuitBreaker.recordFailure();
}

/**
 * Returns a governance status summary for the /health endpoint.
 */
export function governanceStatus(state: GovernanceState) {
  return {
    circuitBreaker: state.circuitBreaker.status(),
    budget: state.budget.status(),
  };
}

import express from "express";
import { paymentMiddleware, x402ResourceServer } from "@x402/express";
import { ExactStellarScheme } from "@x402/stellar/exact/server";
import { HTTPFacilitatorClient } from "@x402/core/server";
import { createProxyHandler } from "./proxy.js";
import { buildRouteConfig } from "./routes.js";
import {
  createGovernance,
  governanceMiddleware,
  governanceStatus,
} from "./governance.js";

const PORT = parseInt(process.env.PORT || "3402", 10);
const STELLAR_ADDRESS = process.env.STELLAR_ADDRESS || "";
const OPENZEPPELIN_API_KEY = process.env.OPENZEPPELIN_API_KEY || "";
const STELLAR_NETWORK = process.env.STELLAR_NETWORK || "testnet";
const UPSTREAM_URL = process.env.UPSTREAM_URL || "http://localhost:8402";

if (!STELLAR_ADDRESS || STELLAR_ADDRESS.startsWith("GXXX")) {
  console.error("ERROR: Set STELLAR_ADDRESS to your Stellar public key");
  process.exit(1);
}

if (!OPENZEPPELIN_API_KEY || OPENZEPPELIN_API_KEY === "your_api_key_here") {
  console.error(
    "ERROR: Set OPENZEPPELIN_API_KEY (get one at https://channels.openzeppelin.com/testnet/gen)"
  );
  process.exit(1);
}

const facilitatorUrl =
  STELLAR_NETWORK === "mainnet"
    ? "https://channels.openzeppelin.com/x402"
    : "https://channels.openzeppelin.com/x402/testnet";

const networkId =
  STELLAR_NETWORK === "mainnet" ? "stellar:mainnet" : "stellar:testnet";

// Create facilitator client
const facilitatorClient = new HTTPFacilitatorClient({
  url: facilitatorUrl,
  createAuthHeaders: async () => ({
    verify: { Authorization: `Bearer ${OPENZEPPELIN_API_KEY}` },
    settle: { Authorization: `Bearer ${OPENZEPPELIN_API_KEY}` },
    supported: { Authorization: `Bearer ${OPENZEPPELIN_API_KEY}` },
  }),
});

// Register Stellar payment scheme
const server = new x402ResourceServer(facilitatorClient).register(
  networkId,
  new ExactStellarScheme()
);

const app = express();

// Initialize governance controls
const governance = createGovernance({
  circuitBreaker: { threshold: 3, cooldownMs: 600_000 },
  budget: { dailyLimitUsd: 50 },
  rateLimit: { windowMs: 60_000, maxRequests: 60 },
});

// Health check (unprotected) — includes governance status
app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    gateway: "stellar-x402",
    network: networkId,
    upstream: UPSTREAM_URL,
    governance: governanceStatus(governance),
  });
});

// Build route config for x402 payment gating
const routes = buildRouteConfig(STELLAR_ADDRESS, networkId);

// Apply governance middleware (circuit breaker, budget, rate limit)
app.use("/api", governanceMiddleware(governance));

// Apply x402 payment middleware
app.use(paymentMiddleware(routes, server));

// Proxy all API requests to upstream risk engine
const proxy = createProxyHandler(UPSTREAM_URL, governance);

app.get("/api/v1/market/risk-scores", proxy);
app.get("/api/v1/market/risk-scores/:asset", proxy);
app.get("/api/v1/market/alerts", proxy);
app.get("/api/v1/market/prices", proxy);
app.get("/api/v1/market/historical", proxy);

app.listen(PORT, () => {
  console.log(`\n🛰️  Monitor the Situation — Stellar x402 Gateway`);
  console.log(`   Port:       ${PORT}`);
  console.log(`   Network:    ${networkId}`);
  console.log(`   Upstream:   ${UPSTREAM_URL}`);
  console.log(`   Pay-to:     ${STELLAR_ADDRESS}`);
  console.log(`   Facilitator: ${facilitatorUrl}\n`);
});

# Monitor the Situation

**AI Agent-Powered Geopolitical Market Intelligence, Paid via x402 on Stellar**

An autonomous AI agent that monitors Hyperliquid perpetual futures (commodities, crypto, FX -- 21 assets), computes real-time risk scores, and serves market intelligence through x402 payment-gated API endpoints on the Stellar network.

Agents and users pay per-request with USDC micropayments via Soroban authorization.

> Built for the [Agents on Stellar Hackathon](https://dorahacks.io/hackathon/stellar-agents-x402-stripe-mpp/detail) by [ZERA](https://swarm-ai-safety.com)

---

## Architecture

```
[Hyperliquid DEX]
        |
        v
[Python Risk Engine]  <-- Polls 21 assets, computes composite risk scores
   (FastAPI :8402)
        |
        v
[Stellar x402 Gateway]  <-- Payment gating via @x402/stellar
   (Express :3402)
        |           |
        v           v
[Soroban USDC]  [OpenZeppelin Channels Facilitator]
        |
        v
[Stellar Testnet]
```

**How it works:**

1. The Python risk engine polls Hyperliquid every 5 minutes for 21 assets
2. A composite risk score (0-100) is computed from 4 weighted signals
3. The Express gateway wraps each API endpoint with x402 Stellar payment gating
4. Clients pay USDC micropayments on Stellar to access risk data
5. OpenZeppelin Channels facilitates payment verification and settlement

## Risk Scoring Engine

Each asset gets a composite score (0-100) from four weighted components:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Funding Rate | 30% | Magnitude of hourly funding rate vs threshold |
| Volume Spike | 25% | Current volume vs rolling 12-sample average |
| OI Shift | 25% | Open interest change vs previous snapshot |
| Basis Deviation | 20% | Mark-oracle price spread |

Separate thresholds for crypto and commodity asset classes. Score >= 60 triggers alerts.

## API Endpoints

All endpoints require x402 payment on Stellar testnet (USDC).

| Endpoint | Price | Description |
|----------|-------|-------------|
| `GET /api/v1/market/risk-scores` | $0.01 | All assets with composite risk scores |
| `GET /api/v1/market/risk-scores/:asset` | $0.005 | Deep dive on single asset (+ orderbook, funding history) |
| `GET /api/v1/market/alerts` | $0.005 | Active alerts only (score >= threshold) |
| `GET /api/v1/market/prices` | $0.002 | Raw price snapshot (no risk engine) |
| `GET /api/v1/market/historical` | $0.05+ | Historical analysis with candle data (1-60 day lookback) |

### x402 Payment Flow

```
Client                          Gateway                    Stellar
  |                                |                          |
  |--- GET /api/v1/market/prices -->|                          |
  |                                |                          |
  |<-- 402 Payment Required -------|                          |
  |    (price, network, payTo)     |                          |
  |                                |                          |
  |--- Sign Soroban auth entry --->|                          |
  |    (PAYMENT-SIGNATURE header)  |                          |
  |                                |--- Verify + Settle ----->|
  |                                |<-- Confirmed ------------|
  |                                |                          |
  |<-- 200 + risk data ------------|                          |
```

## Monitored Assets

**Perpetual Futures:** BTC, ETH, PAXG, SOL

**Spot Commodities:** GLD (gold), SLV (silver)

**xyz: Commodity Perps:** WTI Crude (xyz:CL), Brent Oil (xyz:BRENTOIL), Gold (xyz:GOLD), Silver (xyz:SILVER), Copper (xyz:COPPER), Natural Gas (xyz:NATGAS), Platinum (xyz:PLATINUM), Palladium (xyz:PALLADIUM), EUR (xyz:EUR), JPY (xyz:JPY)

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- An OpenZeppelin Channels API key ([get one here](https://channels.openzeppelin.com/testnet/gen))

### 1. Clone and set up Stellar testnet wallet

```bash
git clone https://github.com/swarm-ai-safety/monitor-the-situation-stellar.git
cd monitor-the-situation-stellar/stellar-gateway
npm install

# Auto-generate a Stellar testnet keypair, fund via Friendbot, and add USDC trustline
npm run setup:testnet
```

This generates your keypair, funds it with 10,000 test XLM, and adds a USDC trustline. Copy the output into your `.env` file.

Then get an OpenZeppelin Channels API key at https://channels.openzeppelin.com/testnet/gen and add it to `.env` as `OPENZEPPELIN_API_KEY`.

### 2. Start the Python risk engine

```bash
pip install -e .
uvicorn monitor.server:app --host 0.0.0.0 --port 8402
```

Verify it's running:
```bash
curl http://localhost:8402/health
# {"status":"ok","service":"risk-engine"}
```

### 3. Start the Stellar x402 gateway

```bash
cd stellar-gateway
npm install
cp ../.env.example .env  # or use the gateway's own .env.example
# Edit .env with your config

npm run dev
```

The gateway starts on port 3402 and proxies paid requests to the risk engine on 8402.

### 4. Test the payment flow

```bash
# This should return 402 Payment Required with Stellar payment instructions
curl -v http://localhost:3402/api/v1/market/prices
```

To complete a payment, use a Soroban-enabled wallet (Freighter, Albedo, etc.) to sign the authorization entry and resubmit with the `PAYMENT-SIGNATURE` header.

### 5. Run the automated E2E demo

With both the risk engine (port 8402) and gateway (port 3402) running:

```bash
cd stellar-gateway
npm run demo:e2e
```

This creates a temporary payer wallet, funds it via Friendbot, swaps XLM for USDC on the Stellar testnet DEX, then makes a paid API request through the x402 gateway — demonstrating the complete payment flow end-to-end.

## Project Structure

```
monitor-the-situation-stellar/
+-- monitor/                  # Python risk engine
|   +-- client.py             # Hyperliquid read-only data client
|   +-- risk.py               # Composite risk scoring engine
|   +-- historical.py         # Historical analysis module
|   +-- server.py             # FastAPI server (port 8402)
+-- stellar-gateway/          # TypeScript x402 Stellar gateway
|   +-- src/
|   |   +-- index.ts          # Express server with x402 middleware
|   |   +-- routes.ts         # Payment route configuration
|   |   +-- proxy.ts          # Upstream proxy handler
|   |   +-- governance.ts     # Circuit breaker, budget caps, rate limiting
|   +-- scripts/
|   |   +-- setup-testnet.ts  # Auto-generate Stellar testnet wallet
|   |   +-- demo-e2e.ts       # Automated end-to-end payment demo
|   +-- package.json
+-- pyproject.toml
+-- .env.example
+-- README.md
```

## What's Complete vs. WIP

**Complete:**
- [x] Hyperliquid perps monitor (~1,100 lines Python)
- [x] Composite risk scoring engine (4-component weighted)
- [x] 5 API endpoints with pricing tiers
- [x] Historical analysis module (daily candles + funding stats)
- [x] Stellar x402 gateway (TypeScript/Express)
- [x] Payment route configuration matching upstream pricing
- [x] Governance module (circuit breaker, budget caps, rate limiting)
- [x] Automated Stellar testnet setup script (keypair, Friendbot, USDC trustline)
- [x] End-to-end Stellar testnet payment demo (automated script)

**WIP:**
- [ ] xyz: namespace assets in risk scoring (currently perps only in API)
- [ ] Video demo

## Tech Stack

- **Risk Engine:** Python, FastAPI, hyperliquid-python-sdk
- **Payment Gateway:** TypeScript, Express, @x402/express, @x402/stellar
- **Payment Facilitator:** OpenZeppelin Channels (Stellar testnet)
- **Blockchain:** Stellar (Soroban), USDC (SEP-41)
- **Data Source:** Hyperliquid DEX (public read-only API)

## Historical Validation

Over a 2-month backtest period, the risk engine detected 176 notable events across monitored assets, including 22 high-severity alerts for volume spikes, extreme funding rates, and large price moves.

## License

MIT

---

Built by [ZERA](https://swarm-ai-safety.com) for the [Agents on Stellar Hackathon](https://dorahacks.io/hackathon/stellar-agents-x402-stripe-mpp/detail).

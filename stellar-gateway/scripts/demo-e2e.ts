/**
 * End-to-end x402 Stellar testnet payment demo.
 *
 * Creates a temporary payer wallet, funds it via Friendbot + testnet USDC,
 * then makes a paid API request through the x402 gateway.
 *
 * Prerequisites:
 *   - Gateway running on localhost:3402 (npm run dev)
 *   - Python risk engine running on localhost:8402
 *
 * Usage: npx tsx scripts/demo-e2e.ts
 */

import * as StellarSdk from "@stellar/stellar-sdk";
import { createEd25519Signer } from "@x402/stellar";
import { ExactStellarScheme } from "@x402/stellar/exact/client";
import { x402Client, x402HTTPClient } from "@x402/core/client";

const GATEWAY_URL = process.env.GATEWAY_URL || "http://localhost:3402";
const HORIZON_TESTNET = "https://horizon-testnet.stellar.org";
const FRIENDBOT_URL = "https://friendbot.stellar.org";
const USDC_ISSUER = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5";

async function main() {
  console.log("=== x402 Stellar Testnet E2E Demo ===\n");

  // 1. Create payer wallet
  console.log("1. Creating temporary payer wallet...");
  const payer = StellarSdk.Keypair.random();
  console.log(`   Address: ${payer.publicKey()}`);

  // 2. Fund via Friendbot
  console.log("2. Funding via Friendbot...");
  const fundResp = await fetch(`${FRIENDBOT_URL}?addr=${payer.publicKey()}`);
  if (!fundResp.ok) {
    throw new Error(`Friendbot failed: ${await fundResp.text()}`);
  }
  console.log("   Funded with 10,000 test XLM.");

  // 3. Add USDC trustline
  console.log("3. Adding USDC trustline...");
  const horizon = new StellarSdk.Horizon.Server(HORIZON_TESTNET);
  const account = await horizon.loadAccount(payer.publicKey());
  const usdcAsset = new StellarSdk.Asset("USDC", USDC_ISSUER);

  const trustlineTx = new StellarSdk.TransactionBuilder(account, {
    fee: StellarSdk.BASE_FEE,
    networkPassphrase: StellarSdk.Networks.TESTNET,
  })
    .addOperation(StellarSdk.Operation.changeTrust({ asset: usdcAsset }))
    .setTimeout(30)
    .build();

  trustlineTx.sign(payer);
  await horizon.submitTransaction(trustlineTx);
  console.log("   USDC trustline added.");

  // 4. Swap XLM → USDC on testnet DEX via path payment
  console.log("4. Swapping XLM for USDC on testnet DEX...");
  const freshAccount = await horizon.loadAccount(payer.publicKey());
  const swapTx = new StellarSdk.TransactionBuilder(freshAccount, {
    fee: StellarSdk.BASE_FEE,
    networkPassphrase: StellarSdk.Networks.TESTNET,
  })
    .addOperation(
      StellarSdk.Operation.pathPaymentStrictSend({
        sendAsset: StellarSdk.Asset.native(),
        sendAmount: "10", // 10 XLM
        destination: payer.publicKey(),
        destAsset: usdcAsset,
        destMin: "0.01", // accept any amount of USDC
      })
    )
    .setTimeout(30)
    .build();

  swapTx.sign(payer);
  await horizon.submitTransaction(swapTx);

  // Check USDC balance
  const balances = (await horizon.loadAccount(payer.publicKey())).balances;
  const usdcBal = balances.find(
    (b: any) => b.asset_code === "USDC" && b.asset_issuer === USDC_ISSUER
  );
  console.log(`   USDC balance: ${(usdcBal as any)?.balance || "0"}`);

  // 5. Make initial request to get 402 response
  console.log("\n5. Requesting /api/v1/market/prices (expect 402)...");
  const initialResp = await fetch(`${GATEWAY_URL}/api/v1/market/prices`);
  console.log(`   Status: ${initialResp.status}`);

  if (initialResp.status !== 402) {
    throw new Error(`Expected 402, got ${initialResp.status}`);
  }

  // 6. Parse payment requirements from PAYMENT-REQUIRED header
  const paymentRequiredHeader = initialResp.headers.get("payment-required");
  if (!paymentRequiredHeader) {
    throw new Error("Missing PAYMENT-REQUIRED header");
  }

  const paymentRequired = JSON.parse(
    Buffer.from(paymentRequiredHeader, "base64").toString("utf8")
  );

  console.log(`   x402 Version: ${paymentRequired.x402Version}`);
  console.log(`   Network: ${paymentRequired.accepts[0].network}`);
  console.log(`   Price: ${paymentRequired.accepts[0].amount} (stroops)`);
  console.log(`   Pay-to: ${paymentRequired.accepts[0].payTo}`);

  // 7. Create x402 client and sign payment
  console.log("\n6. Signing x402 payment with payer wallet...");
  const signer = createEd25519Signer(payer.secret(), "stellar:testnet");
  const stellarScheme = new ExactStellarScheme(signer);

  const client = new x402Client()
    .register("stellar:testnet", stellarScheme);
  const httpClient = new x402HTTPClient(client);

  const paymentPayload = await httpClient.createPaymentPayload(paymentRequired);
  const paymentHeaders = httpClient.encodePaymentSignatureHeader(paymentPayload);

  console.log("   Payment signed successfully.");
  console.log(`   Payload version: ${paymentPayload.x402Version}`);

  // 8. Retry with payment
  console.log("\n7. Retrying with payment signature...");
  const paidResp = await fetch(`${GATEWAY_URL}/api/v1/market/prices`, {
    headers: {
      ...paymentHeaders,
      Accept: "application/json",
    },
  });

  console.log(`   Status: ${paidResp.status}`);

  if (paidResp.status === 200) {
    const data = await paidResp.json();
    console.log("\n=== SUCCESS: Paid API response received ===");
    console.log(`   Gateway: ${data.gateway}`);
    console.log(`   Payment network: ${data.payment_network}`);
    console.log(
      `   Data keys: ${Object.keys(data).filter((k) => k !== "gateway" && k !== "payment_network").join(", ")}`
    );

    // Check for settlement response
    const settleHeader = paidResp.headers.get("x-payment-response");
    if (settleHeader) {
      const settle = JSON.parse(
        Buffer.from(settleHeader, "base64").toString("utf8")
      );
      console.log(`\n   Settlement TX: ${settle.txHash || settle.transaction || "N/A"}`);
      console.log(`   Network: ${settle.network || "stellar:testnet"}`);
    }
  } else {
    const body = await paidResp.text();
    console.log(`\n   Response body: ${body}`);

    if (paidResp.status === 502) {
      console.log(
        "\n   Note: 502 means the payment was accepted but the upstream risk engine"
      );
      console.log(
        "   is not running. The Stellar payment flow itself worked correctly."
      );
      console.log("   Start the Python risk engine to get full e2e data flow.");
    }
  }

  console.log("\n=== Demo Complete ===");
}

main().catch((err) => {
  console.error("\nDemo failed:", err.message || err);
  process.exit(1);
});

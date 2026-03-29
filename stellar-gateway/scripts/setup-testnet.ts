/**
 * Stellar testnet setup script.
 *
 * Generates a keypair, funds it via Friendbot, and adds a USDC trustline.
 * Outputs the keys and a .env snippet you can paste into your config.
 *
 * Usage: npx tsx scripts/setup-testnet.ts
 */

import * as StellarSdk from "@stellar/stellar-sdk";

const FRIENDBOT_URL = "https://friendbot.stellar.org";
const HORIZON_TESTNET = "https://horizon-testnet.stellar.org";
const USDC_ISSUER = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5";

async function main() {
  // 1. Generate keypair
  const pair = StellarSdk.Keypair.random();
  const publicKey = pair.publicKey();
  const secret = pair.secret();

  console.log("Generated Stellar testnet keypair:");
  console.log(`  Public:  ${publicKey}`);
  console.log(`  Secret:  ${secret}`);
  console.log();

  // 2. Fund via Friendbot
  console.log("Funding account via Friendbot...");
  const fundResp = await fetch(`${FRIENDBOT_URL}?addr=${publicKey}`);
  if (!fundResp.ok) {
    const body = await fundResp.text();
    throw new Error(`Friendbot funding failed (${fundResp.status}): ${body}`);
  }
  console.log("Account funded with 10,000 test XLM.");
  console.log();

  // 3. Add USDC trustline
  console.log("Adding USDC trustline...");
  const server = new StellarSdk.Horizon.Server(HORIZON_TESTNET);
  const account = await server.loadAccount(publicKey);

  const usdcAsset = new StellarSdk.Asset("USDC", USDC_ISSUER);

  const tx = new StellarSdk.TransactionBuilder(account, {
    fee: StellarSdk.BASE_FEE,
    networkPassphrase: StellarSdk.Networks.TESTNET,
  })
    .addOperation(StellarSdk.Operation.changeTrust({ asset: usdcAsset }))
    .setTimeout(30)
    .build();

  tx.sign(pair);
  await server.submitTransaction(tx);
  console.log("USDC trustline added successfully.");
  console.log();

  // 4. Output .env snippet
  console.log("=== Add to your .env file ===");
  console.log(`STELLAR_ADDRESS=${publicKey}`);
  console.log(`STELLAR_SECRET=${secret}`);
  console.log(`STELLAR_NETWORK=testnet`);
  console.log();
  console.log(
    "Next: Get an OpenZeppelin Channels API key at https://channels.openzeppelin.com/testnet/gen"
  );
  console.log("Then set OPENZEPPELIN_API_KEY in your .env file.");
}

main().catch((err) => {
  console.error("Setup failed:", err.message || err);
  process.exit(1);
});

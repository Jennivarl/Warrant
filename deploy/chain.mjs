// The one place the network and the signing key are decided.
//
// Both deploy.mjs and demo.mjs import this, so there is no second copy of
// the chain settings to drift.
import { readFileSync } from "node:fs";
import { Wallet } from "ethers";
import { privateKeyToAccount } from "viem/accounts";
import { createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";

export const RPC = "https://studio-next.genlayer.com/api";
export const CHAIN_ID = 61997;
export const EXPLORER = "https://explorer-studio-dev.genlayer.com";

// These contracts are built for GenVM v0.3, and the only public network
// running that engine is Studio Next. Bradbury runs v0.2.11 and answers a
// v0.3 deploy with "runner py-genlayer:5jycge... not found", which the CLI
// still reports as a successful deploy, so the network is not a choice.
//
// genlayer-js ships Studio's older dev network under another id and RPC, so
// its settings are taken and pointed at Studio Next.
export const studioNext = {
  ...studioDevnet,
  id: CHAIN_ID,
  name: "GenLayer Studio Next",
  rpcUrls: { default: { http: [RPC] } },
  blockExplorers: { default: { name: "Studio Next Explorer", url: EXPLORER } },
};

/** The signing account, from a keystore named by the environment. */
export async function signer() {
  const path = process.env.GENLAYER_KEYSTORE;
  const password = process.env.GENLAYER_PASSWORD;
  if (!path || !password) {
    throw new Error("set GENLAYER_KEYSTORE to a keystore file and GENLAYER_PASSWORD to its password");
  }
  const home = process.env.HOME ?? process.env.USERPROFILE ?? "";
  const json = readFileSync(path.replace(/^~/, home), "utf8");
  const wallet = await Wallet.fromEncryptedJson(json, password);
  // An account object, so genlayer-js signs here. Given a plain address
  // string it routes the transaction to a browser wallet instead, which is
  // what a front end wants and what a script cannot do.
  return privateKeyToAccount(wallet.privateKey);
}

export async function connect() {
  const account = await signer();
  return { account, client: createClient({ chain: studioNext, account }) };
}

export const rpc = async (method, params) => {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const body = await res.json();
  if (body.error) throw new Error(`${method}: ${JSON.stringify(body.error)}`);
  return body.result;
};

export const balance = async (who) => BigInt(await rpc("eth_getBalance", [who, "latest"]));
export const fmt = (wei) => `${(Number(wei) / 1e18).toFixed(6)} GEN`;

export const gen = (amount) => {
  const [whole, frac = ""] = String(amount).split(".");
  return BigInt(whole) * 10n ** 18n + BigInt((frac + "0".repeat(18)).slice(0, 18) || "0");
};

/**
 * Wait for consensus, then say what actually happened.
 *
 * `txExecutionResultName` is the field that matters. A transaction can be
 * FINALIZED by consensus and still have finished with an error, which is how
 * a failed deploy comes back looking like a successful one: the CLI prints a
 * contract address for a deploy whose runner the network does not even have.
 *
 * Studio Next has no `gen_getTransactionReceipt`, so the receipt comes from
 * the client rather than from a raw RPC call.
 */
export async function waitFor(client, txId, { until = "finalized" } = {}) {
  console.log("tx", txId);
  const receipt = await client.waitForTransactionReceipt({
    hash: txId,
    waitUntil: until,
    interval: 5000,
    retries: 240,
  });
  const outcome = receipt?.txExecutionResultName ?? String(receipt?.txExecutionResult);
  console.log(`  ${receipt?.status_name ?? receipt?.status}, execution ${outcome}`);
  return { ...receipt, outcome, ok: outcome === "FINISHED_WITH_RETURN" };
}


/**
 * Send a write with a fee estimate.
 *
 * The estimate is advice rather than a gate: the simulator refuses some
 * calls it cannot run, and a call that pays nobody does not need the message
 * allocation it returns. A call that does pay a wallet needs it, so the
 * better estimate is tried first and the generic one is the fallback. The
 * consensus contract rejects a zero fee outright, so some fee is always sent.
 */
export async function estimateFees(client, args) {
  const { messageAllocations, ...call } = args;
  const extra = messageAllocations ? { messageAllocations } : {};
  // When a payout is declared, the generic estimate is the one to use: the
  // write estimate accepts the allocation and still reports
  // `totalMessageFees: 0`, so the transaction carries the allocation with no
  // pool behind it and dies with `out_of message_fee total # external`.
  if (messageAllocations) {
    try {
      const priced = await client.estimateTransactionFees(extra);
      return {
        distribution: priced.distribution,
        messageAllocations,
        feeValue: priced.feeValue,
      };
    } catch {
      return undefined;
    }
  }
  try {
    const est = await client.estimateTransactionFeesForWrite({ ...call, ...extra });
    return {
      distribution: est.distribution,
      messageAllocations: est.messageAllocations ?? messageAllocations ?? [],
      feeValue: est.feeValue,
    };
  } catch {
    // The generic estimate does not simulate, so it still prices any
    // allocation it is handed. That is what a payout needs.
    try {
      const base = await client.estimateTransactionFees(extra);
      return {
        distribution: base.distribution,
        messageAllocations: base.messageAllocations ?? messageAllocations ?? [],
        feeValue: base.feeValue,
      };
    } catch {
      return undefined;
    }
  }
}

export const txUrl = (tx) => `${EXPLORER}/tx/${tx}`;
export const addressUrl = (a) => `${EXPLORER}/address/${a}`;

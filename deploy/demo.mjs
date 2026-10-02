// The live contract, one step per run, so every result is checked before the next.
//
//   npm install
//   export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
//   export GENLAYER_PASSWORD=...           # never stored in this repo
//
//   node deploy/demo.mjs show
//   node deploy/demo.mjs open    <name> <beneficiary> <feed_id> <key> <conditions> <deadline> <GEN>
//   node deploy/demo.mjs release <deal_id>
//   node deploy/demo.mjs refund  <deal_id>
//
// A deal is decided against a reading held by a SIGNAL contract, whose
// address is in live.json along with this contract's own. Taking the reading
// is SIGNAL's job: https://github.com/Jennivarl/Signal
import { readFileSync } from "node:fs";
import {
  MESSAGE_ALLOCATION_ROOT_PARENT_INDEX,
  MessageType,
  encodeExternalMessageFeeParams,
} from "genlayer-js";
import { balance, connect, estimateFees, fmt, gen, txUrl, waitFor } from "./chain.mjs";

const live = JSON.parse(readFileSync(new URL("./live.json", import.meta.url), "utf8"));
const { signal: SIGNAL, warrant: WARRANT } = live;
if (!SIGNAL || !WARRANT) throw new Error("deploy/live.json has no addresses yet");

const { account, client } = await connect();

const view = (address, fn, args = []) => client.readContract({ address, functionName: fn, args });

/**
 * The fee a payout needs reserved before it can leave the contract.
 *
 * Paying a wallet is an external message, and a transaction that does not
 * reserve a budget for one dies with `out_of message_fee total # external`.
 * Neither fee estimator produces that allocation on its own, so it is
 * declared here and handed to the estimate, which prices it in.
 *
 * An allocation that goes unused costs nothing: a release that decides
 * against paying simply never emits the message, and the fee comes back.
 */
const PAYOUT_GAS_LIMIT = 200_000n;
const PAYOUT_MAX_GAS_PRICE = 400_000_000n;

function payoutAllocation(recipient) {
  return {
    messageType: MessageType.External,
    // The transfer is emitted on finalization, not on acceptance.
    onAcceptance: false,
    parentIndex: MESSAGE_ALLOCATION_ROOT_PARENT_INDEX,
    recipient,
    // A zero budget is rejected as ExternalAllocationInvalid, so it has to
    // cover the gas the message may burn at the price cap it declares.
    budget: PAYOUT_GAS_LIMIT * PAYOUT_MAX_GAS_PRICE,
    feeParams: encodeExternalMessageFeeParams({
      gasLimit: PAYOUT_GAS_LIMIT,
      maxGasPrice: PAYOUT_MAX_GAS_PRICE,
    }),
  };
}

async function write(functionName, args, value = 0n, payTo = undefined) {
  const allocations = payTo ? [payoutAllocation(payTo)] : undefined;
  const fees = await estimateFees(client, {
    address: WARRANT,
    functionName,
    args,
    value,
    ...(allocations ? { messageAllocations: allocations } : {}),
  });
  const txId = await client.writeContract({
    address: WARRANT,
    functionName,
    args,
    value,
    // A reading is one web fetch per validator, and a rotation costs a round.
    consensusMaxRotations: 8,
    ...(fees ? { fees: { ...fees, ...(allocations ? { messageAllocations: allocations } : {}) } } : {}),
  });
  const receipt = await waitFor(client, txId);
  console.log("  ", txUrl(txId));
  if (!receipt.ok) console.log("   returned nothing:", receipt.outcome);
  return receipt;
}

async function show() {
  console.log("committed", (await view(WARRANT, "reserves")).committed);
  for (const id of await view(WARRANT, "deal_list")) {
    const deal = await view(WARRANT, "get_deal", [id]);
    const conditions = deal.conditions.map((c) => `${c.field} ${c.op} ${c.want}`).join("; ");
    console.log(`deal ${id} [${deal.state}] ${conditions} | saw ${deal.saw || "-"} | ${deal.reason || "-"}`);
  }
}

/**
 * Check that a decided deal actually moved the money.
 *
 * A state that says `paid` while the balance did not move would be the worst
 * failure this system could have, and it would be silent, so it is worth
 * asserting rather than assuming. The contract changes state before it moves
 * value precisely so that this comparison means something.
 */
function checkValueMoved(step, wasState, nowState, expected, before, after) {
  const decided = step === "release" ? "paid" : "refunded";
  const moved = after - before;
  // Only a deal that left `open` during this call should have moved money.
  // A deal that was already decided is expected to revert and move nothing.
  if (wasState !== "open") {
    if (moved === 0n) console.log(`nothing moved, which is correct: the deal was already ${wasState}`);
    else console.log(`WARNING: the deal was already ${wasState} and the balance still moved by ${fmt(moved)}`);
    return;
  }
  if (nowState !== decided) {
    if (moved === 0n) console.log(`nothing moved, which is correct for a deal still ${nowState}`);
    else console.log(`WARNING: the deal is ${nowState} and yet the balance moved by ${fmt(moved)}`);
    return;
  }
  if (moved === expected) console.log(`value moved: ${fmt(moved)}, exactly the deposit`);
  else console.log(`WARNING: the deal says ${decided} but the balance moved by ${fmt(moved)}, expected ${fmt(expected)}`);
}

const [step, ...rest] = process.argv.slice(2);
console.log("wallet", account.address, fmt(await balance(account.address)), "\n");

if (step === "open") {
  const [name, beneficiary, feed, key, conditions, deadline, amount] = rest;
  await write("open_deal", [name, beneficiary, SIGNAL, feed, key, conditions, deadline], gen(amount));
} else if (step === "release" || step === "refund") {
  const [dealId] = rest;
  const before = await view(WARRANT, "get_deal", [dealId]);
  // Whose balance should move, so the proof is an amount rather than a log line.
  const who = step === "release" ? before.beneficiary : before.depositor;
  const held = await balance(who);
  console.log(`${step === "release" ? "beneficiary" : "depositor"} ${who} before`, fmt(held));

  await write(step, [dealId], 0n, who);

  const after = await view(WARRANT, "get_deal", [dealId]);
  const now = await balance(who);
  console.log("after ", fmt(now));
  checkValueMoved(step, before.state, after.state, BigInt(before.amount), held, now);
} else if (step !== "show") {
  console.log("steps: show, open, release, refund");
  process.exit(1);
}

console.log("");
await show();

// Deploy one bundle to Studio Next and print its address.
//
//   export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
//   export GENLAYER_PASSWORD=...
//   node deploy/deploy.mjs contracts/signal_bundle.py
//
// Then put the address in deploy/live.json.
//
// Build the bundle first: python -m deploy.build_bundle
import { readFileSync } from "node:fs";
import { addressUrl, balance, connect, estimateFees, fmt, waitFor } from "./chain.mjs";

const path = process.argv[2];
if (!path) throw new Error("usage: node deploy/deploy.mjs contracts/<name>_bundle.py");

const code = readFileSync(path, "utf8");
const lines = code.split("\n");
// A deploy over the gas cap returns a hash and then simply does not exist,
// and a comment on line two fails with "trailing characters", so both are
// worth checking before paying for the attempt.
if (lines[0] !== "# v0.3.0" || !lines[1].startsWith('# { "Depends"')) {
  throw new Error("the runner header is not the first two lines of " + path);
}
if (lines[2].startsWith("#")) throw new Error("line 3 is a comment, which GenVM reads as part of the header");

const { account, client } = await connect();
console.log("deploying", path, `(${code.length} bytes)`);
console.log("from", account.address, fmt(await balance(account.address)));

const fees = await estimateFees(client, { code, args: [] });
const txId = await client.deployContract({ code, args: [], ...(fees ? { fees } : {}) });
const receipt = await waitFor(client, txId);

// For a deploy the new contract is the transaction's recipient.
const address = receipt?.to_address ?? receipt?.recipient;
if (!receipt.ok) {
  console.log("\nthe deploy did NOT succeed:", receipt.outcome);
  console.log("ask the node what went wrong:");
  console.log(`  curl -s -X POST ${process.env.RPC ?? "https://studio-next.genlayer.com/api"} -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"gen_dbg_traceTransaction","params":[{"txId":"${txId}"}]}'`);
  process.exit(1);
}
console.log("\naddress", address);
console.log(addressUrl(address));

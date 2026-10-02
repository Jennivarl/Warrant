// Check that the live contract runs the code in this repo.
//
//   node deploy/verify.mjs
//
// The deploy transaction carries the contract's source, so this needs no
// trust in the address, the explorer or this README: it fetches what was
// deployed, hashes it, and compares that with the bundle on disk.
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { connect } from "./chain.mjs";

const live = JSON.parse(readFileSync(new URL("./live.json", import.meta.url), "utf8"));
const bundle = new URL("../contracts/" + live.bundle, import.meta.url);

const { client } = await connect();
const tx = await client.getTransaction({ hash: live.deployTx });
const deployed = Buffer.from(tx.data.contract_code, "base64").toString("utf8");
const local = readFileSync(bundle, "utf8");

const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
console.log("contract      ", live.contract);
console.log("deploy tx     ", live.deployTx);
console.log("on chain      ", deployed.length, "bytes, sha256", sha(deployed));
console.log("in this repo  ", local.length, "bytes, sha256", sha(local));
console.log(deployed === local ? "\nIDENTICAL" : "\nDIFFERENT: rebuild with python -m deploy.build_bundle");
// exitCode rather than exit(), so Node closes its sockets cleanly on Windows.
process.exitCode = deployed === local ? 0 : 1;

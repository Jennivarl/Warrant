# WARRANT

Money that moves only on a fact consensus derived.

A deposit names, once and for all, the exact questions that release it: which SIGNAL contract, which feed, which key, which conditions, who gets paid, and what period is covered. After that nobody contributes anything.

```
open_deal(name, beneficiary, signal, feed_id, key, conditions, covers_until)  payable
release(deal_id)                                                             pay, or say why not
refund(deal_id)                                                              take it back after the deadline
get_deal(deal_id)                                                            free view
```

`release` takes no argument but the deal's own name. It reads the attested record out of [SIGNAL](https://github.com/Jennivarl/Signal) and either the stored values satisfy every condition or they do not. There is no oracle to call, no signature to supply, and no caller input that can change the answer.

That last part is the point. An escrow that accepts a value from whoever triggers it has not removed the trusted party, it has moved it to that caller.

| | |
|---|---|
| Contract | [`0x91D94D7281baFc3513c536810779B6b34D710Dd9`](https://explorer-studio-dev.genlayer.com/address/0x91D94D7281baFc3513c536810779B6b34D710Dd9) |
| Reads facts from | SIGNAL at [`0xb4EA63892D4F8eA3D552486541CE4Deb6Db596BC`](https://explorer-studio-dev.genlayer.com/address/0xb4EA63892D4F8eA3D552486541CE4Deb6Db596BC) |
| Network | GenLayer Studio Next, chain id 61997, `https://studio-next.genlayer.com/api` |
| Engine | Consensus v0.6, GenVM v0.3, runner `py-genlayer:5jycge...` |
| Tests | 148, `python -m pytest -q` |
| Live page | [jennivarl.github.io/Warrant](https://jennivarl.github.io/Warrant/), which reads both contracts in your browser |
| Deployed source | byte-identical to [`contracts/warrant_bundle.py`](contracts/warrant_bundle.py), sha256 `8f96563b...`, checked by `node deploy/verify.mjs` |

---

## What protects each side

| rule | what it prevents |
|---|---|
| paid once | state changes before value moves, and a deal leaves `open` exactly once |
| nothing invented | only a reading with status `read` can pay. `absent` and `unreadable` never do |
| nothing stale | the reading must be newer than the deposit, so a record taken before the deal existed cannot trigger it |
| nothing unsettled | the reading must have existed for an hour, so a payout cannot act on one consensus could still undo |
| nothing stuck | after the deadline the depositor takes it back, so a source that goes quiet cannot strand the money |
| one way out | `release` closes on exactly the boundary `refund` opens, so one and only one transition is ever available and the two can never race for the same deposit |
| nobody squats | a deal id is namespaced by its depositor, so nobody can open a deal under a name someone else is using |
| no burn | a payout to the zero address is refused at the counter |
| no typo that means "never" | every condition's field is checked against SIGNAL's published fields when the deal is opened |

A condition that is not met is not an error. It leaves the deal open and records why, because a claim that fails today may hold tomorrow, and burning the deal on a first look would be a denial of service with extra steps.

---

## All conditions must hold

Real cover asks more than one question at once. A deal carries up to three conditions and every one of them has to hold.

```
incident.impact eq critical; incident.status eq resolved
```

Each clause is a field path the feed publishes, one of `eq ne ge gt le lt`, and a value. Evaluation stops at the first failure and names it, so "it did not pay" is never the whole answer:

```
incident.status is investigating, which is not eq resolved
```

There is no `or`. A payout that could be triggered two different ways is two deals, and keeping them separate means each deposit is backed by exactly the question it names.

`eq` and `ne` compare text exactly. The ordering comparisons are integer only: comparing `"10"` with `"9"` as text pays the wrong side, and floats would introduce a rounding rule nobody agreed to. A value that is not a plain ASCII integer fails an ordering comparison rather than being guessed at. A field the reading does not carry is seen as empty, which is never a reason to pay.

---

## What consensus derived, and what was copied

| stored field | where it comes from | derived or copied |
|---|---|---|
| `conditions` | parsed from the depositor's own call, stored canonically | the depositor's commitment, fixed at open |
| `amount` | `gl.message.value`, the value actually attached to the transaction | derived |
| `depositor` | `gl.message.sender_address` | derived |
| `opened_at`, `decided_at` | the transaction's own `datetime`, part of the message | derived |
| `state` | the contract's own transition, which happens before value moves | derived |
| `saw` | the values read out of SIGNAL's stored record | derived by SIGNAL's validators, read here |
| `reason` | composed from the comparison the contract performed | derived |

Nothing here is supplied by whoever calls `release`, and nothing is taken on a leader's word. The fact itself was derived by SIGNAL's validators, each of which fetched the document for itself.

---

## The storage view, and the settling hour

A payout should only act on a reading that can no longer be rolled back. The natural way to ask for that is a cross-contract read of SIGNAL's finalized storage. On Studio Next that read times out.

Shown with a throwaway probe contract whose two methods differ in nothing but the storage view, reading the same key of the same SIGNAL:

| view | transaction | result |
|---|---|---|
| default | [`0x5c6dcfe2...`](https://explorer-studio-dev.genlayer.com/tx/0x5c6dcfe234f7c26141e14867b6b2902059a563ae90494ff7f1ba1a4de39f41cc) | `FINISHED_WITH_RETURN` |
| `LATEST_FINALIZED` | [`0x1aaccb97...`](https://explorer-studio-dev.genlayer.com/tx/0x1aaccb970988179aefcf40bac205fc8561316beaa69caa3d0de88ed95ed2513c) | `TIMEOUT` |

An earlier deployment of this contract did use `LATEST_FINALIZED`, and its `release` timed out on chain: [`0xe83145f0...`](https://explorer-studio-dev.genlayer.com/tx/0xe83145f0de3b7b0e2c9566cd148dbbac67cc89f2adca2300843da036e8f70aa9). Nothing was lost, because state changes before value moves and the deal simply stayed open, but the payout path did not work. That is what the settling hour replaced.

So the margin is taken in time instead, which both contracts can measure from their own messages. A reading pays only once it is 3600 seconds old, which is comfortably past the window in which consensus could still take it back. The cost is that a payout is never instant, and for an escrow that is the right way round.

Timestamps are compared as seconds, never as text. Both are ISO stamps, but Studio Next writes `2026-10-02T15:44:09.038322Z` while a test writes `2026-10-02T15:44:09Z`, and `"Z"` sorts after `"."`, so text comparison is wrong by a hair exactly where it matters. The conversion is integer arithmetic with no library, and the tests check it against the standard library, which the chain cannot import.

---

## Verified live on Studio Next

Every row is a real transaction against the contract above.

| step | transaction | outcome |
|---|---|---|
| Deploy | [`0x4d14dbeb...`](https://explorer-studio-dev.genlayer.com/tx/0x4d14dbeb1c538f4c9c3743c54e5b5eb41724b021ed8ee2b2bc5bfcd4374a805a) | contract live, source byte-identical to the bundle in this repo |
| Open a 1 GEN deal on two conditions | [`0x21da0dcd...`](https://explorer-studio-dev.genlayer.com/tx/0x21da0dcdeccad95b88ead3d3a170b0a71df00010fc2e92f01f7e85ddd939f431) | `incident.impact eq critical; incident.status eq resolved`. Both field names were checked against SIGNAL cross-contract before the deposit was accepted |
| Open a 0.5 GEN deal whose second condition is false | [`0x5db4e24d...`](https://explorer-studio-dev.genlayer.com/tx/0x5db4e24dbe02bebb3e8ea6fa2e27bf1d35397b7806481fbeb7642da010c54028) | `incident.impact eq critical; incident.status eq investigating` |
| Open a deal naming a field the feed does not publish | [`0xc08b7d6b...`](https://explorer-studio-dev.genlayer.com/tx/0xc08b7d6b0a1674097343fa63b2e11fad75922cd419f932662561b640e639e761) | **refused**, `that feed does not publish the field incident.severity`. The deposit was never accepted and `committed` did not move. A typo that would have meant "never pays" was caught at the counter |
| Release 59 seconds after the reading | [`0x7b188f02...`](https://explorer-studio-dev.genlayer.com/tx/0x7b188f024b99a691052ef8bec038c4d7b598cc76ec907fd4c06f3e9b71220c5f) | **refused**: `the reading is 59 seconds old and must be 3600 before it can pay`. Deal still open, beneficiary still 0.000000 GEN |
| Release once the reading had settled | [`0x66cbc8d1...`](https://explorer-studio-dev.genlayer.com/tx/0x66cbc8d1648520710ccb4a9c8ff5db6ae552c448434289be821ae6b8f1e73b8d) | **paid**: the beneficiary went from 0.000000 to exactly 1.000000 GEN. `saw critical; resolved`, reason `every condition holds: critical; resolved`, and `committed` fell from 1.5 to 0.5 GEN |
| Release the same deal again | [`0xa276db11...`](https://explorer-studio-dev.genlayer.com/tx/0xa276db118de97c341ac2f8cb3b52077799eb2595f58f4fb25cb02bf309868c3a) | **reverted**, `deal is already paid: 0xb428...a75a:outage`. The beneficiary still holds exactly 1.000000 GEN |
| Release the deal whose second condition is false | [`0xee9be0e3...`](https://explorer-studio-dev.genlayer.com/tx/0xee9be0e3a030bc087a505524d2948887e63ff0e64fad1c055ee5fd15a1bf23bf) | **refused and said which**: `incident.status is resolved, which is not eq investigating`. The first condition held. Deal still open, nothing moved, and this returned normally rather than erroring, because a condition that does not hold is not a fault |
| Refund that deal before its deadline | [`0x98c1faac...`](https://explorer-studio-dev.genlayer.com/tx/0x98c1faac33e7f097145a0d71102432d2a13207ac776291b46ac13157d2e6690e) | **refused**, `the deadline has not passed yet` |

The reading both deals are decided against was taken by SIGNAL at `2026-10-02T16:07:58Z`: [`0x872aaa90...`](https://explorer-studio-dev.genlayer.com/tx/0x872aaa904ce052c77afaa1e484fd68b9ed626d52226414f70f46270c63e0e5c5). Every validator fetched `https://www.githubstatus.com/api/v2/incidents/zkxwbgr0cnmx.json` and agreed that GitHub rated that incident `critical` and marked it `resolved`.

The beneficiary `0x0f358a8ae1EFc8eBf5a456bf92101f4d33Da33bE` was a freshly generated address holding nothing, so the payout is provable as an amount rather than as a log line.

---

## One way out at a time, and nothing stranded

Two rules have to hold together, and naively they fight each other.

A deposit must never be claimable by both sides at once, so `release` has to close on exactly the boundary `refund` opens. And a reading needs an hour to settle before it can pay, so that value never moves on a reading consensus could still take back.

Put those together with a deadline the depositor types in, and the last hour before it becomes dead: a reading that arrives then can never settle in time, and a deposit that should have paid refunds instead.

So the deadline is not asked for. The depositor says what period they want covered, and the contract derives the claim deadline one day later:

```
open_deal(..., covers_until="2026-10-31")   ->   covers to 2026-10-31, claim by 2026-11-01
```

| moment | release | refund |
|---|---|---|
| during the cover period | open | shut |
| the claim day after it | open | shut |
| from midnight after that, forever | shut | open |

That gives three properties, each held by tests:

- **Exactly one transition is available at any moment.** Never both, so the two can never race for the same deposit. Never neither, so a deposit is always claimable by someone. The suite sweeps that boundary with a fresh deal at each instant.
- **Nothing inside the cover period is ever stranded.** A reading taken in the final second of the period still settles an hour later and has the rest of the claim day to be paid. Tested from the first second of cover to the last.
- **A reading taken after the period cannot pay.** It may be perfectly true about the world, and it says nothing about this deal. The deal's own terms decide what counts, not when somebody happens to call `release`.

An expired deal is refused before SIGNAL is even consulted, so it costs no cross-contract call and no late reading can influence it.

The date arithmetic this rests on is integer only with no library, because every validator has to produce the same answer. It is checked against the standard library on every day from 2020 to 2031.

---

## Paying a wallet needs the fee reserved up front

This is the part that is easy to get wrong, and getting it wrong is how an escrow ends up recording a payout it never made.

A transfer out of the contract is an **external message**, and the transaction has to carry a funded allocation for it. Neither fee estimator produces one on its own, so it has to be declared. Three attempts at the same payout, in order:

| attempt | transaction | result |
|---|---|---|
| No allocation at all | [`0xcd34bdfb...`](https://explorer-studio-dev.genlayer.com/tx/0xcd34bdfb1f334dfbc71d3e1f2d027d4b263ab3f268b845bbfb0bf23023078cfb) | `out_of message_fee total # external`, `message_allocations_count: 0` |
| An allocation, but priced by the write estimator | [`0x47f4bc69...`](https://explorer-studio-dev.genlayer.com/tx/0x47f4bc698f1f65276918124da0ee03ba5b48780ef0b813abe16be8584b5a125b) | `out_of message_fee total # external`, `message_allocations_count: 1`. The allocation was accepted and `totalMessageFees` was still 0, so nothing stood behind it |
| An allocation priced by `estimateTransactionFees` | [`0x66cbc8d1...`](https://explorer-studio-dev.genlayer.com/tx/0x66cbc8d1648520710ccb4a9c8ff5db6ae552c448434289be821ae6b8f1e73b8d) | paid |

Two things were needed. The allocation must carry a non-zero `budget`, because a zero one is rejected as `ExternalAllocationInvalid`. And it must be priced by `client.estimateTransactionFees({ messageAllocations })`, not by `estimateTransactionFeesForWrite`, which accepts the allocation and still reports `totalMessageFees: 0`. [`deploy/chain.mjs`](deploy/chain.mjs) and [`deploy/demo.mjs`](deploy/demo.mjs) do both.

**The contract was never at risk through any of this.** Each failed attempt reverted whole: the deal stayed `open`, `committed` did not move, and no state recorded a payout that had not happened. That is what changing state before moving value buys, and it is why `deploy/demo.mjs` also checks the recipient's balance against the deposit after every decision rather than trusting the receipt.

---

## Checking that the live contract runs this code

The deploy transaction carries the contract's source, so this needs no trust in the address, the explorer or this README:

```sh
node deploy/verify.mjs
```

```
contract       0x91D94D7281baFc3513c536810779B6b34D710Dd9
deploy tx      0x4d14dbeb1c538f4c9c3743c54e5b5eb41724b021ed8ee2b2bc5bfcd4374a805a
on chain       11242 bytes, sha256 cebc7c4202638525e0c1ae9f97f919b75b7f41e77e63b9fd7c109c5413578754
in this repo   11242 bytes, sha256 cebc7c4202638525e0c1ae9f97f919b75b7f41e77e63b9fd7c109c5413578754

IDENTICAL
```

---

## Limits, stated plainly

- **A payout is never instant.** A reading has to be an hour old. That is the price of not having a working finalized-storage read on this network, and it is a delay rather than a refusal: the deal stays open and claimable.
- **Deadlines are whole days**, and `refund` opens strictly after the deadline day, so the deadline day itself is still claimable. A refund that opened at midnight on the deadline would let a depositor pull the money back while the condition could still be met that same day.
- **The deposit is the payout.** There is no pool, no premium and no leverage. One deal, one amount, one beneficiary.
- **Three conditions per deal**, all joined by and. Storage is paid for and a longer list is usually two deals.
- **The fact is only as good as its source.** WARRANT proves every validator saw the same document. It cannot prove the publisher told the truth. The feed id names who chose the source.
- **A feed that goes quiet means a refund, not a payout.** That is the safe direction, and it is why the deadline exists.
- **No appeal.** If the source published something wrong and it was read, the deal pays on it. Choosing a source you trust is the depositor's job, done once, in the open.
- **An earlier deployment holds 1 GEN** against a deal whose release used the finalized view and timed out. It is refundable after its 2026-10-31 deadline, and it is superseded: the addresses above are the ones this repo builds.

---

## The live page

[jennivarl.github.io/Warrant](https://jennivarl.github.io/Warrant/) shows the whole loop with nothing cached: the three statuses SIGNAL can record, the deal and its conditions, and the payout. Every value on it is read from the chain in the visitor's browser when the page loads, so it cannot drift from what the contracts actually say.

It is one HTML file with no build step, which is deliberate. There is no toolchain to rot, and nothing that can go stale between a deploy and the chain.

```sh
cd site && python -m http.server 8000     # or just open index.html
```

---

## Running it

```sh
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m deploy.build_bundle          # contracts/warrant_bundle.py, the file that deploys
python -m pytest -q                    # 148 tests, no chain and no network
```

Direct mode loads one contract per process, so SIGNAL is answered in the tests by a stub that speaks the same cross-contract protocol the chain uses: the contract really does call out, and the reply really is calldata encoded. What the stub buys is states that are tedious to produce for real, including a reading that is stale, absent, unreadable, too fresh, or carrying a timestamp that cannot be parsed.

Every test in `test_warrant.py` is about money: who can move it, when, how often, and what happens when the fact never arrives.

To drive the live copy:

```sh
npm install
export GENLAYER_KEYSTORE=~/.genlayer/keystores/mykey.json
export GENLAYER_PASSWORD=...
node deploy/demo.mjs open outage <beneficiary> <feed_id> <key> 'incident.impact eq critical' 2026-10-31 1
node deploy/demo.mjs release <deal_id>
```

Addresses live in [`deploy/live.json`](deploy/live.json). The password is read from the environment and is never stored in this repo.

---

## Layout

```
contracts/conditions.py       the pure part: comparisons, condition parsing, timestamps. No chain, no network
contracts/warrant.py          the contract
contracts/warrant_bundle.py   the two pasted together, which is what deploys. Generated
deploy/build_bundle.py        builds the bundle; a test fails if the committed one is stale
deploy/chain.mjs              network and signing, in one place
deploy/deploy.mjs             deploy the bundle
deploy/demo.mjs               open, release and refund against a live copy
deploy/verify.mjs             prove the live contract runs this exact bundle
site/index.html               the live page, one file, no build step
test/                         148 tests
```

GenVM loads a single source file with no access to siblings, so the bundle exists to paste the modules together. Comments and docstrings are stripped from the deployed copy, because a deploy costs gas per byte and a bundle over the cap returns a transaction hash and then simply does not exist. Edit the modules, never the bundle: a test regenerates it and fails if the committed copy has drifted.

## License

MIT

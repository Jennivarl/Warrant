# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
"""
WARRANT. Money that moves only on a fact consensus derived.

A deposit names, once and for all, the exact questions that release it:

    which SIGNAL contract, which feed, which key, which conditions
    (all of which must hold), who gets paid, and by when.

After that nobody contributes anything. `release` takes no arguments beyond
the deal's own name, reads the attested record out of SIGNAL, and either the
stored values satisfy every condition or they do not. There is no oracle to
call, no signature to supply and no caller input that can change the answer,
which is the whole point: an escrow that accepts a value from whoever
triggers it has simply moved the trust to that caller.

The rules that protect the two sides:

    paid once          state changes before value moves, and a deal leaves
                       `open` exactly once
    nothing invented   only a reading with status `read` can pay; `absent`
                       and `unreadable` never do
    nothing stale      the reading must be newer than the deposit, so a
                       record taken before the deal existed cannot trigger it
    nothing unsettled  the reading must have existed for an hour, so a
                       payout cannot act on one consensus could still undo
    nothing stuck      after the deadline the depositor takes it back, so a
                       source that goes quiet cannot strand the money
    one way out        `release` closes on the same boundary `refund` opens,
                       so exactly one transition is ever available and the
                       two can never race for the same deposit
    nobody squats      a deal id is namespaced by its depositor, so nobody
                       can open a deal under a name someone else is using

A condition that is not met is not an error. It leaves the deal open and
says why, because a claim that fails today may hold tomorrow and burning the
deal on a first look would be a denial of service with extra steps.
"""

from dataclasses import dataclass

import genlayer as gl
from genlayer.types import *

from contracts.conditions import (
    READ,
    SETTLING_SECONDS,
    end_of_day,
    epoch_seconds,
    evaluate,
    join_conditions,
    next_day,
    parse_conditions,
)

STATE_OPEN = "open"
STATE_PAID = "paid"
STATE_REFUNDED = "refunded"


@gl.storage.allow
@dataclass
class Deal:
    depositor: Address
    beneficiary: Address
    amount: u256
    signal: Address
    feed: str
    key: str
    # "field op value; field op value", every one of which must hold.
    conditions: str
    # The last day the fact may be read. The depositor's own choice.
    covers_until: str
    # The last day a claim may be made, derived as the day after the cover
    # period so that every reading inside it has a full day to be claimed.
    deadline: str
    opened_at: str
    state: str
    # Filled in when the deal is decided. Empty until then.
    decided_at: str
    # The values the deal was decided on, in condition order.
    saw: str
    reason: str


class Warrant(gl.contract.Contract):
    deals: gl.storage.TreeMap[str, Deal]
    deal_ids: gl.storage.DynArray[str]
    # What is committed to open deals. The contract never pays out more than
    # it holds, so this is what it refuses to double-spend.
    committed: u256

    def __init__(self):
        self.committed = 0

    # ----------------------------------------------------------------
    # opening
    # ----------------------------------------------------------------

    @gl.public.write.payable
    def open_deal(
        self,
        name: str,
        beneficiary: str,
        signal: str,
        feed_id: str,
        key: str,
        conditions: str,
        covers_until: str,
    ) -> dict:
        """
        Lock the value sent against a set of conditions on one feed.

        `covers_until` is the last day the fact may be read. The claim
        deadline is one day later and the contract works it out, so nothing
        is ever stranded by a reading that arrived late in the period.

        Conditions read "field op value; field op value", and all of them
        must hold: real cover asks more than one question at once, such as
        rated major AND lasted at least four hours.

        Every field is checked against the SIGNAL contract now, while the
        depositor can still fix a typo, rather than at release time when a
        wrong field would quietly mean "never pays".
        """
        label = name.strip().lower()
        if not label or len(label) > 40:
            raise gl.vm.UserError("name the deal")
        depositor = gl.message.sender_address
        deal_id = depositor.as_hex.lower() + ":" + label
        if deal_id in self.deals:
            raise gl.vm.UserError("that deal already exists")

        amount = gl.message.value
        if amount <= 0:
            raise gl.vm.UserError("send the deposit with this call")

        try:
            wanted = parse_conditions(conditions)
        except ValueError as bad:
            raise gl.vm.UserError(str(bad))

        covers = covers_until.strip()
        if not _valid_date(covers):
            raise gl.vm.UserError("the cover period must end on a YYYY-MM-DD date")
        if covers < _today():
            raise gl.vm.UserError("the cover period must not already be over")
        # The claim deadline is derived rather than asked for. A reading needs
        # time to settle before it can pay, so a depositor who was asked for
        # the deadline directly would have to leave that headroom by hand, and
        # a reading taken late on the last covered day could never pay. One
        # extra day gives every reading inside the cover period a full day to
        # be claimed, and the depositor only has to say what they want covered.
        claim_by = next_day(covers)
        if claim_by is None:
            raise gl.vm.UserError("the cover period must end on a YYYY-MM-DD date")

        paid_to = _address(beneficiary)
        if paid_to.as_hex.lower() == "0x" + "00" * 20:
            raise gl.vm.UserError("a payout to the zero address would burn the deposit")
        signal_at = _address(signal)
        fid = feed_id.strip().lower()

        # Ask SIGNAL what this feed actually publishes. A field the feed never
        # declares can never be read, so a deal naming one could only ever
        # expire, and the depositor should learn that here.
        try:
            described = gl.contract.get_at(signal_at).view().get_feed(fid)
        except Exception:
            raise gl.vm.UserError("that SIGNAL contract has no feed " + fid)
        published = [str(f) for f in described["fields"]]
        for field, _op, _want in wanted:
            if field not in published:
                raise gl.vm.UserError("that feed does not publish the field " + field)

        self.deals[deal_id] = Deal(
            depositor=depositor,
            beneficiary=paid_to,
            amount=amount,
            signal=signal_at,
            feed=fid,
            key=key.strip(),
            conditions=join_conditions(wanted),
            covers_until=covers,
            deadline=claim_by,
            opened_at=_stamp(),
            state=STATE_OPEN,
            decided_at="",
            saw="",
            reason="",
        )
        self.deal_ids.append(deal_id)
        self.committed = self.committed + amount
        return _as_dict(deal_id, self.deals[deal_id])

    # ----------------------------------------------------------------
    # deciding
    # ----------------------------------------------------------------

    @gl.public.write
    def release(self, deal_id: str) -> dict:
        """
        Pay the beneficiary if the attested reading satisfies every condition.

        Callable by anyone, because nobody can influence the outcome: the
        questions were fixed when the deposit was made, and the answer comes
        out of SIGNAL's storage rather than from whoever made this call.

        Returns the deal either way while the deal is still live. A condition
        that does not hold leaves it open with the reason recorded, so the
        only thing a caller can achieve by calling early is to be told what is
        still missing.

        Refused outright once the deadline has passed: from that moment the
        deposit belongs to the depositor and `refund` is the only way out.
        """
        key = deal_id.strip().lower()
        if key not in self.deals:
            raise gl.vm.UserError("no such deal: " + key)
        deal = self.deals[key]
        if deal.state != STATE_OPEN:
            raise gl.vm.UserError(f"deal is already {deal.state}: {key}")
        # Past the deadline a deal can only be refunded. `refund` opens
        # strictly after the deadline day and this closes on exactly the same
        # boundary, so the two transitions are complementary rather than
        # overlapping: at any moment exactly one of them is available, and
        # there is never a window where whoever calls first decides where the
        # money goes. Checked before SIGNAL is consulted, because an expired
        # deal has no question left to ask.
        if _today() > deal.deadline:
            raise gl.vm.UserError(
                f"the deadline {deal.deadline} has passed; this deal can only be refunded: {key}"
            )

        # The default storage view, not the finalized one. Asking for
        # StorageView.LATEST_FINALIZED is the natural way to refuse a reading
        # that could still be rolled back, and that read times out on Studio
        # Next: see SETTLING_SECONDS for the two probe transactions that show
        # the same call returning one way and timing out the other. The
        # rollback window is covered by elapsed time below instead.
        record = gl.contract.get_at(deal.signal).view().get_reading(deal.feed, deal.key)

        status = str(record["status"])
        if status != READ:
            return self._undecided(key, deal, "the reading says " + (status or "nothing has been read yet"))

        # Timestamps are compared as seconds, never as text: both are ISO
        # stamps, but the network writes fractional seconds and "Z" sorts
        # after ".", so text comparison is wrong by a hair exactly when it
        # matters.
        taken = epoch_seconds(str(record["read_at"]))
        opened = epoch_seconds(deal.opened_at)
        now = epoch_seconds(_stamp())
        if taken is None or opened is None or now is None:
            return self._undecided(key, deal, "a timestamp on this deal or reading could not be read")
        # A reading taken before this deal existed says nothing about it.
        if taken <= opened:
            return self._undecided(key, deal, "the latest reading is older than this deal")
        # The fact has to have been read inside the period that was bought.
        # A later reading says something true about the world and nothing
        # about this deal.
        covers_to = end_of_day(deal.covers_until)
        if covers_to is None:
            return self._undecided(key, deal, "the cover period on this deal could not be read")
        if taken > covers_to:
            return self._undecided(
                key, deal, "the reading was taken after " + deal.covers_until + ", which this deal does not cover"
            )
        if now - taken < SETTLING_SECONDS:
            waited = now - taken
            return self._undecided(
                key,
                deal,
                f"the reading is {waited} seconds old and must be {SETTLING_SECONDS} before it can pay",
            )

        verdict = evaluate(parse_conditions(deal.conditions), record["values"])
        if not verdict["ok"]:
            return self._undecided(key, deal, str(verdict["why"]))

        deal.state = STATE_PAID
        deal.decided_at = _stamp()
        deal.saw = "; ".join(str(v) for v in verdict["saw"])
        deal.reason = "every condition holds: " + deal.saw
        self.deals[key] = deal
        self.committed = self.committed - deal.amount
        # State first, then value.
        _pay(deal.beneficiary, int(deal.amount))
        return _as_dict(key, deal)

    @gl.public.write
    def refund(self, deal_id: str) -> dict:
        """
        Return the deposit after the deadline.

        Anyone may call it, because it can only ever send the money back to
        the depositor. Without it a feed that goes quiet would strand the
        deposit forever.
        """
        key = deal_id.strip().lower()
        if key not in self.deals:
            raise gl.vm.UserError("no such deal: " + key)
        deal = self.deals[key]
        if deal.state != STATE_OPEN:
            raise gl.vm.UserError(f"deal is already {deal.state}: {key}")
        # Strictly after, so the deadline day itself is still claimable. A
        # refund that opened at midnight on the deadline would let a
        # depositor take the money back while the condition could still be
        # met that same day.
        if _today() <= deal.deadline:
            raise gl.vm.UserError("the deadline has not passed yet")

        deal.state = STATE_REFUNDED
        deal.decided_at = _stamp()
        deal.reason = "the deadline passed without the condition being met"
        self.deals[key] = deal
        self.committed = self.committed - deal.amount
        _pay(deal.depositor, int(deal.amount))
        return _as_dict(key, deal)

    def _undecided(self, key: str, deal: Deal, why: str) -> dict:
        """Record why nothing moved, and leave the deal open."""
        deal.reason = why
        self.deals[key] = deal
        return _as_dict(key, deal)

    # ----------------------------------------------------------------
    # reading
    # ----------------------------------------------------------------

    @gl.public.view
    def get_deal(self, deal_id: str) -> dict:
        key = deal_id.strip().lower()
        return _as_dict(key, self.deals[key])

    @gl.public.view
    def deal_list(self) -> list:
        return [d for d in self.deal_ids]

    @gl.public.view
    def count(self) -> int:
        return len(self.deal_ids)

    @gl.public.view
    def reserves(self) -> dict:
        return {"committed": str(self.committed)}


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------


def _stamp() -> str:
    """The transaction's own datetime: identical for every validator."""
    return str(gl.message.raw["datetime"])


def _today() -> str:
    return _stamp()[:10]


def _valid_date(day: str) -> bool:
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        return False
    digits = day[0:4] + day[5:7] + day[8:10]
    for ch in digits:
        if not ("0" <= ch <= "9"):
            return False
    return "01" <= day[5:7] <= "12" and "01" <= day[8:10] <= "31"


def _address(value) -> Address:
    """Calldata turns address-shaped strings into Address; accept both."""
    if hasattr(value, "as_hex"):
        return value
    return Address(str(value).strip())


@gl.evm.contract_interface
class _Wallet:
    """A plain account on the EVM side: nothing to call, only to pay."""

    class View:
        pass

    class Write:
        pass


def _pay(to: Address, amount: int) -> None:
    """
    The one place value leaves this contract.

    Through the EVM interface, which is what actually reaches a plain wallet:
    a GenVM message transfer is addressed to a GenVM contract and never
    arrives. A transaction that pays a wallet must also reserve a fee for
    that outgoing payment, which the client does by estimating fees from a
    simulation before sending.
    """
    _Wallet(to).emit_transfer(value=amount)


def _as_dict(key: str, deal: Deal) -> dict:
    return {
        "deal_id": key,
        "depositor": deal.depositor.as_hex,
        "beneficiary": deal.beneficiary.as_hex,
        "amount": str(deal.amount),
        "signal": deal.signal.as_hex,
        "feed": deal.feed,
        "key": deal.key,
        "conditions": [
            {"field": f, "op": o, "want": w} for f, o, w in parse_conditions(deal.conditions)
        ],
        "covers_until": deal.covers_until,
        "deadline": deal.deadline,
        "opened_at": deal.opened_at,
        "state": deal.state,
        "decided_at": deal.decided_at,
        "saw": deal.saw,
        "reason": deal.reason,
    }

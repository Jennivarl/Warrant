"""
WARRANT run for real in direct mode, against a stand-in SIGNAL.

Direct mode loads one contract per process, so SIGNAL is answered by a stub
that speaks the same cross-contract protocol the chain uses: the contract
really does call out, and the reply really is calldata encoded. What the stub
lets a test do is put SIGNAL into states that are tedious to produce for real,
including a reading that is stale, absent or unreadable.

The live pair, both deployed, is exercised separately in deploy/demo.mjs.

Every test here is about money: who can move it, when, how often, and what
happens when the fact never arrives.
"""

import sys
from pathlib import Path

import pytest

BUNDLE = str(Path(__file__).resolve().parent.parent / "contracts" / "warrant_bundle.py")

DEPOSITOR = b"\xa0" * 20
BENEFICIARY = b"\xb0" * 20
STRANGER = b"\xc0" * 20
SIGNAL_AT = "0x" + ("dd" * 20)

FEED = "0xaa:github"
KEY = "zkxwbgr0cnmx"
FIELDS = ["incident.id", "incident.impact", "incident.minutes"]
DEPOSIT = 1_000
# An EVM transfer, which is how value reaches a plain wallet. v0.2 called
# this EthSend.
EVM_PAY = "EmitExternalMessage"

# What a plain `.view()` asks for. StorageView: DEFAULT 0, LATEST_FINALIZED 1,
# LATEST_DECIDED 2.
VIEW_LATEST_DECIDED = 2

OPENED = "2026-10-01T09:00:00Z"
LATER = "2026-10-02T09:00:00Z"
EARLIER = "2026-09-30T09:00:00Z"
# One second past the hour a reading has to settle for before it can pay.
SETTLED = "2026-10-02T10:00:01Z"

# What the test clock is set to, so a helper can move it forward without
# dragging it back under a test that deliberately set a later date.
_CLOCK = {"now": OPENED}


def set_date(vm, stamp: str) -> None:
    _CLOCK["now"] = stamp
    vm.warp(stamp)
    message = sys.modules.get("genlayer.message")
    if message is not None and isinstance(getattr(message, "raw", None), dict):
        message.raw["datetime"] = stamp
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = stamp


class Oracle:
    """
    A stand-in SIGNAL.

    Answers the two views WARRANT calls, records every transfer the contract
    emits, and remembers which storage state each read asked for, so a test
    can prove the payout reads finalized storage rather than decided storage.
    """

    def __init__(self):
        self.fields = list(FIELDS)
        self.reading = {
            "feed": FEED,
            "key": KEY,
            "status": "read",
            "values": {"incident.id": KEY, "incident.impact": "critical", "incident.minutes": "627"},
            "url": "https://www.githubstatus.com/api/v2/incidents/" + KEY + ".json",
            "read_at": LATER,
        }
        self.transfers = []
        self.views = []

    def set_reading(self, **changes):
        self.reading = {**self.reading, **changes}

    def install(self, vm):
        def hook(_vm, request):
            # Imported here rather than at install time: the SDK only exists
            # once a contract has been loaded, and the stub is installed first.
            from gltest.direct.sdk_compat import import_calldata

            calldata = import_calldata()

            for kind in ("PostMessage", "EthSend", EVM_PAY):
                if kind in request:
                    msg = request[kind]
                    self.transfers.append(
                        {"to": bytes(msg["address"].as_bytes), "value": int(msg["value"]), "kind": kind}
                    )
                    return {"ok": None}

            if "CallContract" in request:
                call = request["CallContract"]
                method = call["calldata"][""]
                self.views.append({"method": method, "storage_view": call.get("storage_view")})
                if method == "get_feed":
                    answer = {
                        "feed_id": FEED,
                        "host": "www.githubstatus.com",
                        "path": "/api/v2/incidents/{key}.json",
                        "fields": self.fields,
                        "creator": "0x" + "aa" * 20,
                        "registered_at": EARLIER,
                        "example_url": "x",
                    }
                else:
                    answer = self.reading
                return bytes([0]) + calldata.encode(answer)
            return None

        vm._gl_call_hook = hook
        return self


@pytest.fixture
def oracle(direct_vm):
    return Oracle().install(direct_vm)


@pytest.fixture
def warrant(direct_vm, direct_deploy, oracle):
    contract = direct_deploy(BUNDLE)
    direct_vm.strict_mocks = False
    set_date(direct_vm, OPENED)
    return contract


def released(vm, warrant, deal_id):
    """
    Release once the reading has settled, which is the ordinary case.

    A payout waits an hour after the reading, so almost every test about
    what a release decides has to be standing past that point. Tests about
    the waiting rule itself call `warrant.release` directly.
    """
    if _CLOCK["now"] < SETTLED:
        set_date(vm, SETTLED)
    return warrant.release(deal_id)


def open_deal(vm, warrant, name="d1", who=DEPOSITOR, amount=DEPOSIT, **kw):
    args = dict(
        beneficiary="0x" + BENEFICIARY.hex(),
        signal=SIGNAL_AT,
        feed_id=FEED,
        key=KEY,
        conditions="incident.impact eq critical",
        deadline="2026-10-31",
    )
    args.update(kw)
    vm.value = amount
    try:
        with vm.prank(who):
            return warrant.open_deal(name, **args)
    finally:
        vm.value = 0


# --------------------------------------------------------------------
# opening a deal
# --------------------------------------------------------------------


def test_a_deposit_is_locked_against_one_question(direct_vm, warrant):
    got = open_deal(direct_vm, warrant)

    assert got["deal_id"] == "0x" + DEPOSITOR.hex() + ":d1"
    assert got["state"] == "open"
    assert got["amount"] == str(DEPOSIT)
    assert got["beneficiary"].lower() == "0x" + BENEFICIARY.hex()
    assert got["conditions"] == [{"field": "incident.impact", "op": "eq", "want": "critical"}]
    assert got["opened_at"] == OPENED
    assert warrant.reserves()["committed"] == str(DEPOSIT)


def test_a_deal_id_is_namespaced_so_nobody_can_squat_one(direct_vm, warrant):
    mine = open_deal(direct_vm, warrant, name="claim")
    theirs = open_deal(direct_vm, warrant, name="claim", who=STRANGER)
    assert mine["deal_id"] != theirs["deal_id"]
    assert warrant.count() == 2


def test_the_same_depositor_cannot_reuse_a_name(direct_vm, warrant):
    open_deal(direct_vm, warrant)
    with direct_vm.expect_revert("already exists"):
        open_deal(direct_vm, warrant)


def test_a_deal_with_no_deposit_is_refused(direct_vm, warrant):
    with direct_vm.expect_revert("send the deposit"):
        open_deal(direct_vm, warrant, amount=0)


def test_an_unknown_comparison_is_refused(direct_vm, warrant):
    with direct_vm.expect_revert("unknown comparison"):
        open_deal(direct_vm, warrant, conditions="incident.impact roughly critical")


def test_a_malformed_condition_is_refused(direct_vm, warrant):
    with direct_vm.expect_revert("field op value"):
        open_deal(direct_vm, warrant, conditions="incident.impact")


def test_more_conditions_than_a_deal_may_carry_are_refused(direct_vm, warrant):
    too_many = "; ".join(
        ["incident.impact eq critical", "incident.minutes ge 1", "incident.id eq x", "incident.id ne y"]
    )
    with direct_vm.expect_revert("at most"):
        open_deal(direct_vm, warrant, conditions=too_many)


@pytest.mark.parametrize("deadline", ["31-10-2026", "2026-13-01", "nope", "2026-10-1"])
def test_a_deadline_that_is_not_a_date_is_refused(direct_vm, warrant, deadline):
    with direct_vm.expect_revert("YYYY-MM-DD"):
        open_deal(direct_vm, warrant, deadline=deadline)


def test_a_deadline_in_the_past_is_refused(direct_vm, warrant):
    with direct_vm.expect_revert("must be in the future"):
        open_deal(direct_vm, warrant, deadline="2026-09-01")


def test_a_field_the_feed_does_not_publish_is_refused_at_the_counter(direct_vm, warrant, oracle):
    """Otherwise the deal could only ever expire, and the depositor would not know why."""
    with direct_vm.expect_revert("does not publish the field"):
        open_deal(direct_vm, warrant, conditions="incident.invented eq 1")


def test_opening_asks_the_signal_contract_what_the_feed_publishes(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    assert oracle.views[0]["method"] == "get_feed"


# --------------------------------------------------------------------
# releasing
# --------------------------------------------------------------------


def test_a_satisfied_condition_pays_the_beneficiary_exactly_once(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")

    assert got["state"] == "paid"
    assert got["saw"] == "critical"
    assert oracle.transfers == [{"to": BENEFICIARY, "value": DEPOSIT, "kind": EVM_PAY}]
    assert warrant.reserves()["committed"] == "0"


def test_the_payout_reads_the_storage_view_that_works_on_this_network(direct_vm, warrant, oracle):
    """
    Asking for StorageView.LATEST_FINALIZED would be the natural way to
    refuse a reading consensus could still take back, and that read times
    out on Studio Next (see SETTLING_SECONDS for the two probe
    transactions). So the release uses the default view and covers the
    rollback window with elapsed time instead, and this test pins the view
    it actually asks for so the pair cannot drift apart silently.
    """
    open_deal(direct_vm, warrant)
    released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")

    reads = [v for v in oracle.views if v["method"] == "get_reading"]
    assert reads and all(v["storage_view"] == VIEW_LATEST_DECIDED for v in reads)


def test_a_number_condition_is_compared_as_a_number(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant, conditions="incident.minutes ge 240")
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "paid"
    assert got["saw"] == "627"


def test_a_deal_cannot_be_paid_twice(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    deal_id = "0x" + DEPOSITOR.hex() + ":d1"
    released(direct_vm, warrant, deal_id)
    with direct_vm.expect_revert("already paid"):
        released(direct_vm, warrant, deal_id)
    assert len(oracle.transfers) == 1


def test_a_condition_that_is_not_met_leaves_the_deal_open_and_says_why(direct_vm, warrant, oracle):
    oracle.set_reading(values={**oracle.reading["values"], "incident.impact": "minor"})
    open_deal(direct_vm, warrant)
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")

    assert got["state"] == "open"
    assert "minor" in got["reason"]
    assert oracle.transfers == []


@pytest.mark.parametrize("status", ["absent", "unreadable", ""])
def test_only_a_real_reading_can_pay(direct_vm, warrant, oracle, status):
    oracle.set_reading(status=status)
    open_deal(direct_vm, warrant)
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")

    assert got["state"] == "open"
    assert oracle.transfers == []


def test_a_reading_older_than_the_deal_cannot_trigger_it(direct_vm, warrant, oracle):
    """
    Otherwise a deposit could be opened against a fact that was already true
    and drained the same moment, which is not a wager, it is a withdrawal.
    """
    oracle.set_reading(read_at=EARLIER)
    open_deal(direct_vm, warrant)
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")

    assert got["state"] == "open"
    assert "older than this deal" in got["reason"]
    assert oracle.transfers == []


def test_a_release_can_succeed_later_once_a_fresh_reading_arrives(direct_vm, warrant, oracle):
    oracle.set_reading(read_at=EARLIER)
    open_deal(direct_vm, warrant)
    deal_id = "0x" + DEPOSITOR.hex() + ":d1"
    assert released(direct_vm, warrant, deal_id)["state"] == "open"

    oracle.set_reading(read_at=LATER)
    assert released(direct_vm, warrant, deal_id)["state"] == "paid"
    assert oracle.transfers == [{"to": BENEFICIARY, "value": DEPOSIT, "kind": EVM_PAY}]


# --------------------------------------------------------------------
# the deadline
# --------------------------------------------------------------------


def test_the_deposit_cannot_be_taken_back_before_the_deadline(direct_vm, warrant):
    open_deal(direct_vm, warrant)
    with direct_vm.expect_revert("has not passed"):
        warrant.refund("0x" + DEPOSITOR.hex() + ":d1")


def test_the_deadline_day_itself_is_still_claimable(direct_vm, warrant, oracle):
    """
    A refund that opened at midnight on the deadline would let a depositor
    pull the money out while the condition could still be met that day.
    """
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-10-31T23:59:59Z")
    with direct_vm.expect_revert("has not passed"):
        warrant.refund("0x" + DEPOSITOR.hex() + ":d1")

    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "paid"


def test_after_the_deadline_the_deposit_goes_back_to_whoever_put_it_up(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-11-01T00:00:01Z")
    got = warrant.refund("0x" + DEPOSITOR.hex() + ":d1")

    assert got["state"] == "refunded"
    assert oracle.transfers == [{"to": DEPOSITOR, "value": DEPOSIT, "kind": EVM_PAY}]
    assert warrant.reserves()["committed"] == "0"


def test_a_refunded_deal_cannot_then_be_released(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    deal_id = "0x" + DEPOSITOR.hex() + ":d1"
    set_date(direct_vm, "2026-11-01T00:00:00Z")
    warrant.refund(deal_id)
    with direct_vm.expect_revert("already refunded"):
        released(direct_vm, warrant, deal_id)
    assert len(oracle.transfers) == 1


def test_a_paid_deal_cannot_then_be_refunded(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    deal_id = "0x" + DEPOSITOR.hex() + ":d1"
    released(direct_vm, warrant, deal_id)
    set_date(direct_vm, "2026-11-01T00:00:00Z")
    with direct_vm.expect_revert("already paid"):
        warrant.refund(deal_id)
    assert len(oracle.transfers) == 1


def test_anyone_may_trigger_either_outcome_because_neither_is_a_choice(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    with direct_vm.prank(STRANGER):
        got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "paid"
    # The stranger gains nothing: the money goes where the deal said.
    assert oracle.transfers == [{"to": BENEFICIARY, "value": DEPOSIT, "kind": EVM_PAY}]


# --------------------------------------------------------------------
# refusals that should read as refusals
# --------------------------------------------------------------------


def test_an_unknown_deal_says_so_rather_than_failing_obscurely(direct_vm, warrant):
    with direct_vm.expect_revert("no such deal"):
        released(direct_vm, warrant, "0xnobody:nothing")
    with direct_vm.expect_revert("no such deal"):
        warrant.refund("0xnobody:nothing")


def test_a_payout_to_the_zero_address_is_refused(direct_vm, warrant):
    with direct_vm.expect_revert("zero address"):
        open_deal(direct_vm, warrant, beneficiary="0x" + "00" * 20)


def test_a_feed_the_signal_contract_does_not_have_is_refused(direct_vm, warrant, oracle):
    """The stand-in raises the way SIGNAL does for an unknown feed."""

    def hook(_vm, request):
        if "CallContract" in request:
            raise Exception("no such feed")
        return None

    direct_vm._gl_call_hook = hook
    with direct_vm.expect_revert("has no feed"):
        open_deal(direct_vm, warrant, name="d2")


# --------------------------------------------------------------------
# more than one condition
# --------------------------------------------------------------------

BOTH = "incident.impact eq critical; incident.minutes ge 240"


def test_a_deal_can_ask_several_questions_at_once(direct_vm, warrant, oracle):
    """Real cover asks about severity and duration together."""
    got = open_deal(direct_vm, warrant, conditions=BOTH)
    assert got["conditions"] == [
        {"field": "incident.impact", "op": "eq", "want": "critical"},
        {"field": "incident.minutes", "op": "ge", "want": "240"},
    ]

    paid = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert paid["state"] == "paid"
    assert paid["saw"] == "critical; 627"
    assert oracle.transfers == [{"to": BENEFICIARY, "value": DEPOSIT, "kind": EVM_PAY}]


def test_every_condition_must_hold_not_merely_one(direct_vm, warrant, oracle):
    oracle.set_reading(values={**oracle.reading["values"], "incident.minutes": "12"})
    open_deal(direct_vm, warrant, conditions=BOTH)

    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "open"
    assert "incident.minutes is 12" in got["reason"]
    assert oracle.transfers == []


def test_the_failing_condition_is_named_so_a_depositor_can_act_on_it(direct_vm, warrant, oracle):
    oracle.set_reading(values={**oracle.reading["values"], "incident.impact": "minor"})
    open_deal(direct_vm, warrant, conditions=BOTH)

    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert "incident.impact is minor" in got["reason"]
    assert "not eq critical" in got["reason"]


def test_a_deal_records_the_values_it_was_decided_on(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant, conditions=BOTH)
    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":d1")
    assert got["saw"] == "critical; 627"
    # Stamped when it was decided, not when it was opened.
    assert got["decided_at"] == SETTLED
    assert got["opened_at"] == OPENED


# --------------------------------------------------------------------
# waiting for the reading to settle
#
# A payout acts on the decided view, because the finalized view times out on
# this network. An hour of elapsed time stands in for it, and these are the
# tests that hold that rule in place.
# --------------------------------------------------------------------


def test_a_reading_that_has_not_settled_yet_does_not_pay(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    set_date(direct_vm, LATER)  # the instant the reading was taken

    got = warrant.release("0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "open"
    assert "must be 3600" in got["reason"]
    assert oracle.transfers == []


def test_the_reason_says_how_long_the_reading_has_existed(direct_vm, warrant, oracle):
    """A depositor should be able to work out when to come back."""
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-10-02T09:30:00Z")

    got = warrant.release("0x" + DEPOSITOR.hex() + ":d1")
    assert "1800 seconds old" in got["reason"]


def test_one_second_short_of_the_hour_does_not_pay(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-10-02T09:59:59Z")

    assert warrant.release("0x" + DEPOSITOR.hex() + ":d1")["state"] == "open"
    assert oracle.transfers == []


def test_exactly_the_hour_pays(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-10-02T10:00:00Z")

    assert warrant.release("0x" + DEPOSITOR.hex() + ":d1")["state"] == "paid"
    assert oracle.transfers == [{"to": BENEFICIARY, "value": DEPOSIT, "kind": EVM_PAY}]


def test_a_deal_that_was_too_fresh_pays_once_the_hour_has_passed(direct_vm, warrant, oracle):
    """Waiting is a delay, never a refusal: the deal stays open and claimable."""
    open_deal(direct_vm, warrant)
    set_date(direct_vm, LATER)
    assert warrant.release("0x" + DEPOSITOR.hex() + ":d1")["state"] == "open"

    set_date(direct_vm, SETTLED)
    assert warrant.release("0x" + DEPOSITOR.hex() + ":d1")["state"] == "paid"


def test_the_fractional_seconds_the_network_writes_are_handled(direct_vm, warrant, oracle):
    """
    Studio Next stamps a reading "2026-10-02T09:00:00.790497Z". Compared as
    text that sorts after "2026-10-02T09:00:00Z", because "Z" sorts after
    ".", which is exactly the kind of hair's-breadth error that pays the
    wrong side.
    """
    oracle.set_reading(read_at="2026-10-02T09:00:00.790497Z")
    open_deal(direct_vm, warrant)
    set_date(direct_vm, "2026-10-02T10:00:01Z")

    assert warrant.release("0x" + DEPOSITOR.hex() + ":d1")["state"] == "paid"


def test_a_timestamp_that_cannot_be_read_pays_nothing(direct_vm, warrant, oracle):
    """An unreadable stamp is a reason to wait, never a reason to pay."""
    oracle.set_reading(read_at="not a timestamp")
    open_deal(direct_vm, warrant)
    set_date(direct_vm, SETTLED)

    got = warrant.release("0x" + DEPOSITOR.hex() + ":d1")
    assert got["state"] == "open"
    assert "could not be read" in got["reason"]
    assert oracle.transfers == []


# --------------------------------------------------------------------
# what the contract believes it owes
#
# `committed` is the only thing standing between this contract and paying
# out more than it holds, so it is worth checking across several deals
# rather than only one.
# --------------------------------------------------------------------


def test_several_open_deals_add_up(direct_vm, warrant):
    open_deal(direct_vm, warrant, name="one", amount=1_000)
    open_deal(direct_vm, warrant, name="two", amount=2_500)
    assert warrant.reserves()["committed"] == "3500"
    assert warrant.count() == 2


def test_paying_one_deal_leaves_the_others_committed(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant, name="one", amount=1_000)
    open_deal(direct_vm, warrant, name="two", amount=2_500)

    paid = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":one")
    assert paid["state"] == "paid"
    # Only the deal that was decided left the books.
    assert warrant.reserves()["committed"] == "2500"
    assert oracle.transfers == [{"to": BENEFICIARY, "value": 1_000, "kind": EVM_PAY}]


def test_a_release_that_decides_nothing_leaves_the_books_alone(direct_vm, warrant, oracle):
    open_deal(direct_vm, warrant, name="one", amount=1_000, conditions="incident.impact eq minor")
    before = warrant.reserves()["committed"]

    got = released(direct_vm, warrant, "0x" + DEPOSITOR.hex() + ":one")
    assert got["state"] == "open"
    assert warrant.reserves()["committed"] == before
    assert oracle.transfers == []


def test_a_deal_already_decided_cannot_be_counted_twice(direct_vm, warrant, oracle):
    """
    The reserve is only sound if `open` is left exactly once. Both ways out
    of `open` are refused a second time, so the subtraction happens once.
    """
    deal_id = "0x" + DEPOSITOR.hex() + ":one"
    open_deal(direct_vm, warrant, name="one", amount=1_000)
    released(direct_vm, warrant, deal_id)
    assert warrant.reserves()["committed"] == "0"

    with direct_vm.expect_revert("already paid"):
        warrant.release(deal_id)
    set_date(direct_vm, "2026-11-01T00:00:01Z")
    with direct_vm.expect_revert("already paid"):
        warrant.refund(deal_id)
    assert warrant.reserves()["committed"] == "0"
    assert len(oracle.transfers) == 1

"""
The arithmetic the money rests on, tested on its own.

No chain and no network here. If any of these are wrong, a deposit pays the
wrong side or never pays at all, which is the one failure this design exists
to prevent.
"""

import pytest

from contracts.conditions import (
    MAX_CONDITIONS,
    OPS,
    READ,
    SETTLING_SECONDS,
    compare,
    epoch_seconds,
    evaluate,
    join_conditions,
    parse_conditions,
    valid_field_path,
)

# What SIGNAL stores for a real GitHub incident, which is what a deal is
# decided against.
REAL = {
    "incident.id": "zkxwbgr0cnmx",
    "incident.impact": "critical",
    "incident.status": "resolved",
    "incident.minutes": "627",
}


# ----------------------------------------------------------------------
# one comparison
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,value,want,expected",
    [
        ("eq", "major", "major", True),
        ("eq", "minor", "major", False),
        ("ne", "minor", "major", True),
        ("ge", "627", "240", True),
        ("ge", "240", "240", True),
        ("gt", "240", "240", False),
        ("le", "10", "9", False),
        ("lt", "9", "10", True),
        ("ge", "-5", "-10", True),
    ],
)
def test_conditions_decide_the_obvious_cases(op, value, want, expected):
    assert compare(op, value, want) is expected


def test_numbers_are_compared_as_numbers_not_as_text():
    # "10" < "9" as text, which would pay the wrong side.
    assert compare("gt", "10", "9") is True


@pytest.mark.parametrize("value", ["", "major", "1.5", "1e3", " 12 x", "٣"])
def test_an_ordering_comparison_on_something_that_is_not_an_integer_is_false(value):
    """Including Arabic-Indic digits, which str.isdigit() accepts and no provider published."""
    assert compare("ge", value, "0") is False


def test_an_unknown_comparison_is_refused_rather_than_defaulting():
    with pytest.raises(ValueError):
        compare("approximately", "1", "1")


def test_every_declared_op_is_implemented():
    for op in OPS:
        assert compare(op, "1", "1") in (True, False)


def test_the_only_status_that_may_pay_is_named_rather_than_inferred():
    """
    A payout tests for this exact string. "not an error" would turn any new
    status SIGNAL learns to publish into a reason to release money.
    """
    assert READ == "read"


# ----------------------------------------------------------------------
# reading a set of conditions
# ----------------------------------------------------------------------


def test_one_condition_parses_to_its_three_parts():
    assert parse_conditions("incident.impact eq critical") == [("incident.impact", "eq", "critical")]


def test_several_conditions_keep_their_order():
    got = parse_conditions("incident.impact eq critical; incident.minutes ge 240")
    assert got == [("incident.impact", "eq", "critical"), ("incident.minutes", "ge", "240")]


def test_spacing_and_empty_clauses_do_not_change_the_meaning():
    assert parse_conditions("  incident.impact   eq   critical ;  ") == parse_conditions(
        "incident.impact eq critical"
    )


def test_a_value_may_contain_spaces_because_providers_publish_such_values():
    assert parse_conditions("incident.name eq Incident with GitHub.com") == [
        ("incident.name", "eq", "Incident with GitHub.com")
    ]


def test_the_stored_form_round_trips():
    """The stored string is what a reader is shown, so it has to mean the same thing."""
    text = "incident.impact eq critical; incident.minutes ge 240"
    assert join_conditions(parse_conditions(text)) == text
    assert parse_conditions(join_conditions(parse_conditions(text))) == parse_conditions(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        ";;",
        "incident.impact",
        "incident.impact eq",
        "incident.impact roughly critical",
        ".impact eq critical",
        "incident..impact eq critical",
        "incident/impact eq critical",
        "x" * 65 + " eq 1",
    ],
)
def test_a_condition_that_cannot_be_decided_is_refused(text):
    with pytest.raises(ValueError):
        parse_conditions(text)


def test_the_same_field_and_comparison_twice_is_refused():
    """Two answers to one question is a contradiction waiting to happen."""
    with pytest.raises(ValueError):
        parse_conditions("incident.impact eq critical; incident.impact eq major")


def test_the_same_field_with_different_comparisons_is_allowed():
    """A range is a real thing to ask for."""
    got = parse_conditions("incident.minutes ge 240; incident.minutes lt 1440")
    assert len(got) == 2


def test_more_conditions_than_a_deal_may_carry_are_refused():
    too_many = "; ".join(f"f{i} eq {i}" for i in range(MAX_CONDITIONS + 1))
    with pytest.raises(ValueError):
        parse_conditions(too_many)


@pytest.mark.parametrize("path", ["incident.impact", "dist-tags.latest", "maintainers.0.name", "a"])
def test_field_paths_a_feed_may_publish_are_accepted(path):
    assert valid_field_path(path) is True


@pytest.mark.parametrize("path", ["", ".a", "a.", "a..b", "a b", "a/b", "x" * 65])
def test_field_paths_that_could_not_be_published_are_refused(path):
    assert valid_field_path(path) is False


# ----------------------------------------------------------------------
# deciding a deal
# ----------------------------------------------------------------------


def test_every_condition_holding_is_the_only_way_to_pay():
    got = evaluate(parse_conditions("incident.impact eq critical; incident.minutes ge 240"), REAL)
    assert got["ok"] is True
    assert got["saw"] == ["critical", "627"]
    assert got["why"] == ""


def test_one_condition_failing_stops_the_payout():
    got = evaluate(parse_conditions("incident.impact eq critical; incident.minutes ge 1440"), REAL)
    assert got["ok"] is False


def test_the_failing_condition_is_named_so_a_depositor_can_act_on_it():
    got = evaluate(parse_conditions("incident.minutes ge 1440"), REAL)
    assert got["why"] == "incident.minutes is 627, which is not ge 1440"


def test_evaluation_stops_at_the_first_failure_rather_than_reporting_the_last():
    got = evaluate(parse_conditions("incident.impact eq minor; incident.minutes ge 240"), REAL)
    assert got["saw"] == ["critical"]
    assert "incident.impact" in got["why"]


def test_a_field_the_reading_does_not_carry_is_never_a_reason_to_pay():
    """
    The field may be missing because the provider dropped it. Missing is not
    equal to anything and is not greater than anything.
    """
    assert evaluate(parse_conditions("incident.invented eq critical"), REAL)["ok"] is False
    assert evaluate(parse_conditions("incident.invented ne critical"), REAL)["ok"] is True
    assert evaluate(parse_conditions("incident.invented ge 0"), REAL)["ok"] is False


def test_a_missing_field_is_reported_as_empty_rather_than_as_nothing():
    got = evaluate(parse_conditions("incident.invented eq critical"), REAL)
    assert got["why"] == "incident.invented is empty, which is not eq critical"


def test_an_empty_reading_pays_nothing():
    assert evaluate(parse_conditions("incident.impact eq critical"), {})["ok"] is False


def test_a_value_is_compared_as_text_so_a_number_is_not_reformatted():
    """SIGNAL stores what the document said. 627 must not become 627.0 on the way in."""
    assert evaluate(parse_conditions("incident.minutes eq 627"), REAL)["ok"] is True
    assert evaluate(parse_conditions("incident.minutes eq 627.0"), REAL)["ok"] is False


# ----------------------------------------------------------------------
# reading a timestamp
#
# A payout waits for a reading to settle, which means subtracting two ISO
# stamps. The contract cannot import a date library, so the arithmetic is
# here and it is checked against the standard library, which the tests can
# import even though the chain cannot.
# ----------------------------------------------------------------------


def reference(stamp: str) -> int:
    import datetime

    return int(datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


@pytest.mark.parametrize(
    "stamp",
    [
        "1970-01-01T00:00:00Z",
        "1999-01-01T00:00:01Z",
        "2000-02-29T12:00:00Z",
        "1900-03-01T00:00:00Z",
        "2024-02-29T23:59:59Z",
        "2026-10-02T14:42:50Z",
        "2026-12-31T23:59:59Z",
        "2100-03-01T00:00:00Z",
    ],
)
def test_a_timestamp_reads_as_the_same_instant_the_standard_library_sees(stamp):
    assert epoch_seconds(stamp) == reference(stamp)


def test_the_fractional_seconds_the_network_writes_are_truncated_not_refused():
    """Studio Next stamps a reading "2026-10-02T14:42:50.790497Z"."""
    assert epoch_seconds("2026-10-02T14:42:50.790497Z") == epoch_seconds("2026-10-02T14:42:50Z")


@pytest.mark.parametrize(
    "stamp",
    [
        "",
        "   ",
        "x",
        "2026-10-02",
        "2026-10-02T14:42",
        "2026-13-02T00:00:00Z",
        "2026-00-10T00:00:00Z",
        "2026-10-02T25:00:00Z",
        "2026-10-02T14:61:00Z",
        "2026-02-29T00:00:00Z",
        "1900-02-29T00:00:00Z",
        "2026-04-31T00:00:00Z",
        "2026/10/02T00:00:00Z",
        None,
    ],
)
def test_a_timestamp_that_cannot_be_read_is_none_rather_than_zero(stamp):
    """Zero would be 1970, which would make every reading look ancient and settled."""
    assert epoch_seconds(stamp) is None


def test_a_leap_second_is_accepted_rather_than_refused():
    assert epoch_seconds("2016-12-31T23:59:60Z") is not None


def test_an_hour_is_the_margin_a_reading_must_clear():
    assert SETTLING_SECONDS == 3600
    taken = epoch_seconds("2026-10-02T09:00:00Z")
    assert epoch_seconds("2026-10-02T09:59:59Z") - taken < SETTLING_SECONDS
    assert epoch_seconds("2026-10-02T10:00:00Z") - taken == SETTLING_SECONDS


def test_the_difference_is_right_across_a_day_and_a_month_boundary():
    """Text comparison and naive arithmetic both go wrong exactly here."""
    assert epoch_seconds("2026-10-03T00:00:00Z") - epoch_seconds("2026-10-02T23:00:00Z") == 3600
    assert epoch_seconds("2026-11-01T00:00:00Z") - epoch_seconds("2026-10-31T23:00:00Z") == 3600
    assert epoch_seconds("2027-01-01T00:00:00Z") - epoch_seconds("2026-12-31T23:00:00Z") == 3600

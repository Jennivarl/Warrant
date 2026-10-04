"""
The pure part of WARRANT. No chain, no network, no model.

A deposit is released by a question asked once and answered by consensus.
Everything that turns an attested reading into "pay" or "not yet" lives in
this file, where it can be tested directly and read by anyone checking what
the money actually depends on.

Every function here is total: it returns an answer for any input rather than
raising, except where a condition is malformed, which is refused when the
deal is opened and the depositor can still fix it.

The one status WARRANT cares about, out of the three SIGNAL publishes:

    read        the document was fetched and the field was present

`absent` (the document was fetched and said nothing) and `unreadable` (the
document could not be fetched) are not facts about the world and must never
move money. WARRANT checks for `read` by name rather than for "not an
error", so a new status added upstream cannot quietly become a payout.
"""

READ = "read"

_MAX_PATH = 64


def valid_field_path(path: str) -> bool:
    """
    A dotted path into a JSON document, like "incident.impact".

    The same shape SIGNAL accepts when a feed declares its fields, because a
    condition that names a path SIGNAL would not accept could never be met.
    """
    if not path or len(path) > _MAX_PATH:
        return False
    if path.startswith(".") or path.endswith(".") or ".." in path:
        return False
    for ch in path:
        if not (("0" <= ch <= "9") or ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch in "._-"):
            return False
    return True


# The comparisons a deal may be given. Deliberately few: each is total, and
# each is decided on the stored reading rather than on anything a caller
# sends at release time.
OPS = ("eq", "ne", "ge", "gt", "le", "lt")


def compare(op: str, value: str, want: str) -> bool:
    """
    Evaluate one condition against one attested value.

    `eq` and `ne` compare text exactly. The ordering comparisons are integer
    only: comparing "10" with "9" as text says the wrong thing, and floats
    would introduce a rounding rule nobody agreed to. A value that is not an
    integer fails an ordering comparison rather than guessing at it.
    """
    if op not in OPS:
        raise ValueError("unknown comparison: " + repr(op))
    if op == "eq":
        return value == want
    if op == "ne":
        return value != want
    if not _is_int(value) or not _is_int(want):
        return False
    left, right = int(value), int(want)
    if op == "ge":
        return left >= right
    if op == "gt":
        return left > right
    if op == "le":
        return left <= right
    return left < right


def _is_int(text: str) -> bool:
    """
    Plain ASCII integers only.

    `str.isdigit()` alone accepts other numeral systems, and a value a
    provider never published as an ASCII number has no business being
    compared as one.
    """
    t = (text or "").strip()
    if t.startswith("-"):
        t = t[1:]
    return bool(t) and t.isdigit() and t.isascii()


# How many conditions one deal may carry. Real cover asks more than one
# question at a time ("rated critical AND resolved"), and a primitive that
# can only ask one is a demo. Three is enough for that and keeps the stored
# form short, which matters because storage is paid for.
MAX_CONDITIONS = 3


def parse_conditions(text: str) -> list:
    """
    Conditions as (field, op, want), from "field op value; field op value".

    Every clause is three parts: a field path the feed publishes, one of the
    comparisons above, and the value to compare against. The value may
    contain spaces, so only the first two words are taken as field and op.

    All conditions must hold. There is no "or": a payout that could be
    triggered two different ways is two deals, and keeping them separate
    means each deposit is backed by exactly the question it names.
    """
    out = []
    for raw in (text or "").split(";"):
        clause = raw.strip()
        if not clause:
            continue
        parts = clause.split()
        if len(parts) < 3:
            raise ValueError("a condition reads: field op value, not " + repr(clause))
        field, op = parts[0], parts[1]
        want = " ".join(parts[2:])
        if not valid_field_path(field):
            raise ValueError("bad field path in condition: " + repr(field))
        if op not in OPS:
            raise ValueError("unknown comparison: " + repr(op))
        if any(field == f and op == o for f, o, _ in out):
            raise ValueError("the same field and comparison twice: " + repr(clause))
        out.append((field, op, want))
    if not out:
        raise ValueError("a deal needs at least one condition")
    if len(out) > MAX_CONDITIONS:
        raise ValueError(f"at most {MAX_CONDITIONS} conditions per deal")
    return out


def join_conditions(conditions: list) -> str:
    """The canonical stored form, which is what a reader is shown."""
    return "; ".join(f"{field} {op} {want}" for field, op, want in conditions)


def evaluate(conditions: list, values: dict) -> dict:
    """
    Check every condition against one attested reading.

    Returns what was seen as well as the verdict, so a deal can record the
    values it was decided on rather than only the answer. Stops at the first
    condition that fails, and says which one, because "it did not pay" is
    not an explanation anybody can act on.

    A field missing from the reading is seen as "", which fails `eq` against
    any real value and fails every ordering comparison. It is never treated
    as a reason to pay.
    """
    saw = []
    for field, op, want in conditions:
        value = str(values[field]) if field in values else ""
        saw.append(value)
        if not compare(op, value, want):
            return {
                "ok": False,
                "saw": saw,
                "why": f"{field} is {value or 'empty'}, which is not {op} {want}",
            }
    return {"ok": True, "saw": saw, "why": ""}


# How long a reading must have existed before it may move money.
#
# A payout should only act on a reading that can no longer be rolled back.
# The natural way to ask for that is a cross-contract read of the other
# contract's finalized storage, and on GenLayer Studio Next that read times
# out: the same call against the same contract returns with the default
# storage view and times out with StorageView.LATEST_FINALIZED. Proven with
# a throwaway probe contract whose two methods differ in nothing else:
#
#   default view     0x5c6dcfe234f7c26141e14867b6b2902059a563ae90494ff7f1ba1a4de39f41cc
#   latest finalized 0x1aaccb970988179aefcf40bac205fc8561316beaa69caa3d0de88ed95ed2513c
#
# So the margin is taken in time instead, which both contracts can measure
# from their own messages: a reading pays only once it is an hour old, which
# is comfortably past the window in which consensus could still take it
# back. The cost is that a payout is never instant, and that is the right
# way round for an escrow.
SETTLING_SECONDS = 3600


def epoch_seconds(stamp: str):
    """
    An ISO-8601 UTC timestamp as a whole number of seconds, or None.

    Only the fixed "YYYY-MM-DDTHH:MM:SS" prefix is read, so a stamp carrying
    fractional seconds is accepted and truncated rather than refused: the
    network writes "2026-10-02T14:42:50.790497Z" and a test writes
    "2026-10-02T14:42:50Z", and those have to mean the same instant.

    Returns None for anything it cannot read, which callers must treat as
    "cannot decide" rather than as zero. Comparing timestamps as text very
    nearly works and then does not, because "Z" sorts after ".".
    """
    s = (stamp or "").strip()
    if len(s) < 19 or s[4] != "-" or s[7] != "-" or s[10] != "T" or s[13] != ":" or s[16] != ":":
        return None
    for i in (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18):
        if not ("0" <= s[i] <= "9"):
            return None
    year, month, day = int(s[0:4]), int(s[5:7]), int(s[8:10])
    hour, minute, second = int(s[11:13]), int(s[14:16]), int(s[17:19])
    if not (1 <= month <= 12) or day < 1 or day > _days_in_month(year, month):
        return None
    # 60 is allowed because an ISO-8601 stamp may carry a leap second.
    if hour > 23 or minute > 59 or second > 60:
        return None
    return _days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second


def _days_in_month(year: int, month: int) -> int:
    """So an impossible date is refused rather than rolling into the next month."""
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _days_from_civil(year: int, month: int, day: int) -> int:
    """
    Days since 1970-01-01, by the usual era arithmetic.

    Integer only and no library, because the same number has to come out on
    every validator. The calendar is the proleptic Gregorian one, which is
    what an ISO-8601 stamp means.
    """
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    year_of_era = y - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def end_of_day(day: str):
    """
    The last second of a calendar day, as epoch seconds, or None.

    A cover period is given in whole days, and the question "was this read
    inside the period" has to be asked against a precise instant.
    """
    stamp = (day or "").strip()
    if len(stamp) != 10:
        return None
    return epoch_seconds(stamp + "T23:59:59Z")


def next_day(day: str):
    """
    The calendar day after this one, as YYYY-MM-DD, or None.

    Used once, to derive a deal's claim deadline from its cover period, so
    the depositor never has to leave headroom by hand.
    """
    at = epoch_seconds((day or "").strip() + "T12:00:00Z")
    if at is None:
        return None
    return _civil_from_days(at // 86400 + 1)


def _civil_from_days(days: int) -> str:
    """
    The inverse of _days_from_civil, by the same era arithmetic.

    Integer only and no library, because every validator has to produce the
    same string.
    """
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    day_of_era = z - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    year = year_of_era + era * 400
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    mp = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * mp + 2) // 5 + 1
    month = mp + (3 if mp < 10 else -9)
    if month <= 2:
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}"

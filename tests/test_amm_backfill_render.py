"""`history amm-backfill` in words: every reason something was left out is a line of its own, with its count.

Review of 12 Nov: the backfill's newer reasons (`buys_non_coin`: a swap into a stablecoin or wrapped SOL; `too_busy`: a
stretch with more transactions than --max-stretch-txs; the month's limit; a dry run, which fetches nothing) reached
the rendered text only inside the free `note`, and the older ones stood in one clause of the `stretches` line. An agent
that reads a job's text now finds one line a reason: how many, of what, why, and the result's own key for it."""

from snipetech import report as R
from tests.test_amm_backfill import M, run, world  # noqa: F401  (`world`: the fixture of the backfill's own tests)

PRICES = {"getTransaction": {"credits": 1, "source": "measured on 12 Nov"}}


def _result(**over) -> dict:
    """A backfill's result as `amm_backfill.backfill` returns it: one wallet, nothing left out."""
    keys = ("wanted", "held", "fetched", "on_tape", "no_cursor", "no_pool", "listing_incomplete", "incomplete")
    keys += ("too_busy", "left", "buys_outside_tape", "buys_too_recent", "buys_non_coin", "transactions", "trades")
    stretches = dict.fromkeys(keys, 0)
    stretches |= {"wanted": 12, "held": 3, "fetched": 4, "transactions": 900, "trades": 350}
    stretches |= over.pop("stretches", {})
    out = {
        "dry_run": False, "days": 2, "after_secs": 300, "min_sol": 1.0, "max_stretch_txs": 8000,
        "cut_off": 1_794_300_000, "tape_from": 1_794_000_000,
        "wallets": [{"wallet": "W" * 44, "name": "Ansem", "listed": 600, "fetched": 585, "to_fetch": 0, "missing": 0,
                     "buys": 21}],
        "stopped": None, "note": "", "stretches": stretches, "too_busy": [],
        "requests": {"getSignaturesForAddress": 30, "getTransaction": 1485},
        "credits": {"counted": 1515, "max": 20000, "this_month": 21000, "month_max": 50_000_000, "prices": PRICES},
    }  # fmt: skip
    return out | over


def _reasons(text: str) -> dict[str, str]:
    """The lines under "left out", by the key each ends with."""
    lines = text.split("\n")
    at = next(k for k, line in enumerate(lines) if line.startswith("  left out"))
    out = {}
    for line in lines[at + 1 :]:
        if not line.startswith("    "):
            break
        out[line.rsplit("(", 1)[1].rstrip(")")] = line.strip()
    return out


def test_a_run_that_left_nothing_out_has_no_such_lines():
    text = R.render_amm_backfill(_result())
    assert "left out" not in text
    assert "  stretches: 12 wanted, 3 held before, 4 fetched now (900 pool transactions, 350 trades)" in text.split(
        "\n"
    )


def test_a_buy_of_a_non_coin_is_a_line_with_its_count():
    one = _reasons(R.render_amm_backfill(_result(stretches={"buys_non_coin": 1})))
    assert list(one) == ["buys_non_coin"]
    assert one["buys_non_coin"].startswith("1 buy of something that is not a coin")
    assert "no stretch" in one["buys_non_coin"]
    three = _reasons(R.render_amm_backfill(_result(stretches={"buys_non_coin": 3})))
    assert three["buys_non_coin"].startswith("3 buys of something that is not a coin")


def test_stretches_too_busy_are_a_line_with_their_count_their_size_and_their_buys():
    busy = [
        {"mint": "M1", "from": 1, "to": 2, "transactions": 20_000, "buys": [{"wallet": "a", "sig": "s1", "ts": 1}]},
        {"mint": "M2", "from": 3, "to": 4, "transactions": 11_000,
         "buys": [{"wallet": "a", "sig": "s2", "ts": 3}, {"wallet": "b", "sig": "s3", "ts": 3}]},
    ]  # fmt: skip
    got = _reasons(R.render_amm_backfill(_result(stretches={"too_busy": 2}, too_busy=busy)))
    line = got["too_busy"]
    assert line.startswith("2 stretches with more than 8,000 transactions")
    assert "not fetched" in line and "31,000 transactions" in line and "3 buys" in line
    one = _reasons(R.render_amm_backfill(_result(stretches={"too_busy": 1}, too_busy=busy[:1])))["too_busy"]
    assert one.startswith("1 stretch with more than 8,000 transactions") and "1 buy" in one and "1 buys" not in one


def test_what_a_limit_cut_off_is_a_line_that_names_the_limit():
    month = _result(stretches={"left": 5}, stopped="month_credits")
    line = _reasons(R.render_amm_backfill(month))["left"]
    assert line.startswith("5 stretches not reached") and "the month's limit of 50,000,000 credits" in line
    own = _result(stretches={"left": 1}, stopped="max_credits")
    line = _reasons(R.render_amm_backfill(own))["left"]
    assert line.startswith("1 stretch not reached") and "the limit of 20,000 credits" in line and "month" not in line
    # the run's last line still says why it stopped, as it did
    assert "  stopped: the month's limit of 50,000,000 credits for backfills was reached." in R.render_amm_backfill(
        month
    )


def test_a_dry_run_says_how_many_stretches_it_listed_and_did_not_fetch():
    estimate = {"wallet_transactions": 40, "stretches_known": 9, "stretches_too_busy": 2,
                "stretch_transactions_known": 700, "stretches_expected": 1.5, "stretch_transactions_expected": 300,
                "credits": 1040, "assumed": [], "seconds_at_the_rate": 60}  # fmt: skip
    dry = _result(dry_run=True, estimate=estimate, too_busy=[
        {"mint": "M1", "from": 1, "to": 2, "transactions": 9000, "buys": []},
        {"mint": "M2", "from": 3, "to": 4, "transactions": 9500, "buys": []}],
        stretches={"fetched": 0, "trades": 0, "transactions": 700, "too_busy": 2, "no_cursor": 1})  # fmt: skip
    got = _reasons(R.render_amm_backfill(dry))
    # nine stretches were to be listed: two too busy, one with no place to list back from, six listed whole
    assert got["dry_run"].startswith("6 stretches listed and not fetched") and "700 pool transactions" in got["dry_run"]
    assert got["too_busy"].startswith("2 stretches") and got["no_cursor"].startswith("1 stretch ")
    # a real run has no such line
    assert "dry_run" not in _reasons(R.render_amm_backfill(_result(stretches={"no_cursor": 1})))


def test_the_older_reasons_are_lines_of_their_own_too():
    s = {"on_tape": 1, "buys_too_recent": 2, "buys_outside_tape": 3, "no_cursor": 4, "no_pool": 5,
         "listing_incomplete": 6, "incomplete": 7, "buys_non_coin": 8}  # fmt: skip
    got = _reasons(R.render_amm_backfill(_result(stretches=s)))
    assert set(got) == set(s)
    for key, n in s.items():
        noun = "buy" if key.startswith("buys_") else "stretch"
        assert got[key].startswith(f"{n} {noun}"), got[key]
    assert got["on_tape"] == "1 stretch already on the recorder's tape (on_tape)"
    assert got["buys_too_recent"] == "2 buys too recent for a whole stretch (buys_too_recent)"


def test_real_results_render_their_reasons(world, monkeypatch):  # noqa: F811
    """The result dicts of real runs over the backfill tests' world (one PumpSwap buy, four transactions in its
    stretch): too busy, a dry run, a buy of a non-coin."""
    from snipetech import positions

    chain, store, _ = world
    busy = _reasons(R.render_amm_backfill(run(chain, store, max_stretch_txs=3)))
    assert list(busy) == ["too_busy"]
    assert busy["too_busy"].startswith("1 stretch with more than 3 transactions")
    assert "4 transactions" in busy["too_busy"] and "1 buy" in busy["too_busy"]
    dry = _reasons(R.render_amm_backfill(run(chain, store, dry_run=True)))
    assert list(dry) == ["dry_run"]
    assert dry["dry_run"].startswith("1 stretch listed and not fetched") and "4 pool transactions" in dry["dry_run"]
    monkeypatch.setitem(positions.NON_COIN_MINTS, M, "a stablecoin")
    none = _reasons(R.render_amm_backfill(run(chain, store)))
    assert list(none) == ["buys_non_coin"] and none["buys_non_coin"].startswith("1 buy of something that is not a coin")

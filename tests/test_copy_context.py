"""What a known wallet's buy is worth by its context (owner, 10 Nov: "look into stats of the coin. For example if 2 kols
are in, copy trade when the second one gets in, or the volume of the coin or the certain type of day"). Every first buy
of a labelled wallet in coins launched in the window is an event; we enter `entry_slots` after its slot (a block late,
where our copies land) and mark the price at each horizon. The events are grouped by how many known wallets were in,
the coin's volume before, its age, the hour, the leader's size and what other wallets bought later in the leader's own
block. A batch job: the read is too long for the agents' 30 s SQL."""

import time

from snipetech.analysis.context import copy_context
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


def _coin(st, i, *, fast=0.0):
    """kol1 buys, the crowd follows and sells out; kol2 buys, the crowd follows and a big buy lifts the coin."""
    m = Market(st, addr(f"cx-coin-{i}"), addr(f"cx-dev-{i}"), NOW - 3000 + i * 100, 70_000_000 + i * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("kol1"), 1.0, 5)
    if fast:
        m.buy(addr("fastbot"), fast, 5)  # in kol1's own block, after it
    m.buy(addr("crowd"), 1.0, 6)
    m.sell(addr("crowd"), 1.0, 8)
    m.buy(addr("kol1"), 1.0, 9)  # a known wallet's second buy is not a new event
    m.buy(addr("kol2"), 2.0, 10)
    m.buy(addr("crowd2"), 1.0, 11)
    m.buy(addr("whale"), 10.0, 20)
    m.commit()
    return m


def _store(n=3, **kw):
    st = Store(":memory:")
    st.set_label(addr("kol1"), "KOL one", "kol", "file")
    st.set_label(addr("kol2"), "tracked two", "wallet", "file")
    for i in range(n):
        st.set_label(
            addr(f"cx-dev-{i}"), "a labelled creator", "wallet", "file"
        )  # a coin's creator is never its leader
        _coin(st, i, **kw)
    return st


def test_the_second_known_wallet_in_is_scored_apart_from_the_first():
    st = _store()
    try:
        out = copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=0.5)
        assert out["events"] == 6 and out["coins"] == 3 and out["horizons"] == [3, 12] and out["entry_slots"] == 1
        by = {r["bucket"]: r for r in out["by"]["known_wallets_in"]}
        first, second = by["1"], by["2"]
        assert first["n"] == 3 and second["n"] == 3 and "3" not in by
        assert first["ret_3"] < 0  # entered after the crowd, which sold out by slot +3
        assert second["ret_3"] == 0.0 and second["ret_12"] > 20 and second["win_12"] == 1.0  # the whale came after
        assert second["med_12"] > 20 and second["ex3_12"] is None  # nothing is left without the 3 best of 3
    finally:
        st.close()


def test_events_are_also_grouped_by_the_coins_volume_age_hour_size_and_group():
    st = _store()
    try:
        out = copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=0.5)
        assert set(out["by"]) == {"known_wallets_in", "volume_before_sol", "age", "hour_utc", "leader_buy_sol",
                                  "same_slot_followers_sol", "group"}  # fmt: skip
        vol = {r["bucket"]: r["n"] for r in out["by"]["volume_before_sol"]}
        assert vol == {"<5": 6}  # 0.5 SOL before kol1, 4.5 before kol2 (sells count as volume)
        assert {r["bucket"]: r["n"] for r in out["by"]["age"]} == {"<30 s": 6}
        assert {r["bucket"]: r["n"] for r in out["by"]["leader_buy_sol"]} == {"1-3": 6}
        assert {r["bucket"]: r["n"] for r in out["by"]["group"]} == {"kol": 3, "wallet": 3}
        hours = out["by"]["hour_utc"]
        assert sum(r["n"] for r in hours) == 6 and all(len(r["bucket"]) == 5 and r["bucket"][2] == "-" for r in hours)
    finally:
        st.close()


def test_what_other_wallets_buy_later_in_the_leaders_own_block_is_its_own_split():
    """10 Nov: 6Z91 buys in the copied wallet's block and sells 5 s later; a leader with such followers leaves nothing
    for a copy that lands a block late."""
    st = _store(fast=3.0)
    try:
        out = copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=0.5)
        same = {r["bucket"]: r["n"] for r in out["by"]["same_slot_followers_sol"]}
        assert same == {"0": 3, "2-10": 3}  # kol2 alone in its block; kol1 with the fast bot's 3 SOL behind it
    finally:
        st.close()


def test_small_buys_and_buys_too_fresh_for_the_longest_horizon_are_left_out():
    st = _store()
    try:
        assert copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=1.5)["events"] == 3  # kol2's 2 SOL only
        assert copy_context(st, NOW, hours=2, horizons=(3, 100_000), min_sol=0.5)["events"] == 0
    finally:
        st.close()


def test_the_agents_may_start_it_and_the_cli_prints_json(tmp_path, capsys):
    import json

    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "copy-context" in RESEARCH_COMMANDS and "copy-context" in ALLOWED and "copy-context" not in OWNER_COMMANDS
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label(addr("kol1"), "KOL one", "kol", "file")
        st.set_label(addr("kol2"), "tracked two", "wallet", "file")
        for i in range(3):
            _coin(st, i)
    finally:
        st.close()
    argv = ["--db", str(db), "copy-context", "--hours", "2", "--horizons", "3,12", "--min-sol", "0.5"]
    assert cli.main([*argv, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["events"] == 6 and out["by"]["known_wallets_in"][1]["bucket"] == "2"
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert "known_wallets_in" in text and "ret_12" in text


def test_a_copy_right_behind_the_leader_in_its_own_block_enters_before_the_followers():
    """10 Nov, our fills: 20 of 24 live copies since 7 Nov landed in the leader's own block, 0.3 trades behind. Scored
    there, the copy enters at the price the leader's buy left, ahead of what the block's other followers add."""
    st = _store(fast=3.0)
    try:
        late = copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=0.5)
        behind = copy_context(st, NOW, hours=2, horizons=(3, 12), min_sol=0.5, behind_leader=True)
        assert behind["entry"] == "right behind the leader's buy" and late["entry"] == "1 slot(s) after the leader's"
        first = lambda out: next(r for r in out["by"]["known_wallets_in"] if r["bucket"] == "1")
        assert first(behind)["ret_3"] > first(late)["ret_3"]  # the fast bot's 3 SOL and the crowd came after our entry
        assert first(behind)["ret_3"] > 0 > first(late)["ret_3"]
    finally:
        st.close()

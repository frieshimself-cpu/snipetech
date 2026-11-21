"""`lab --each`: score what each wallet's own copy sniper copies (21 Nov 2026).

A copy sniper copies every buy of its wallet above its floor and under its market-cap ceiling. The hand lab's wallet
rule fired once a coin, at the first followed wallet in, so a wallet that bought after another followed wallet had
none of those coins scored: the 5 SOL labs of 21 Nov gave Cented 220 copies in the week, only the coins no other
candidate had bought first, where his sniper would have copied every one of his buys. `--each` gives every followed
wallet its own moments (the qualify lab's form, `lab._first_buys`), and those moments now keep the rule's own gates on
each buy: the market-cap ceiling, the largest buy, the curve and the launchpad were `_fire`'s alone, so a per-wallet
score with a ceiling scored buys the sniper refuses."""

import json
import time

from snipetech.analysis.lab import Trigger, _fire, _first_buys
from snipetech.models import LAUNCHLAB
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr
from .test_bid_landing import LEAD, LEAD2, _coin, _store

NOW = int(time.time())
RULE = {"kind": "kol", "group": "wallet", "sol": 1}


def _pumped(store: Store, mint: str, venue: str | None = None):
    """A coin three followed wallets buy: mc-1 at about 31 SOL of market cap, then someone pumps it past 150, then
    mc-2 (1 SOL, about 163) and mc-big (3 SOL, about 177)."""
    m = Market(store, addr(mint), addr(f"{mint}-dev"), NOW - 4000, 31_000_000)
    if venue is not None:
        m.venue = venue
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("mc-1"), 1.0, 2)
    m.buy(addr("mc-pump"), 40.0, 3)
    m.buy(addr("mc-2"), 1.0, 4)
    m.buy(addr("mc-big"), 3.0, 5)
    m.commit()
    launch = store.launch(addr(mint))
    return MarketPath.build(store.trades_for_mint(addr(mint)), launch), launch


GROUPS = {"cand": {addr("mc-1"), addr("mc-2"), addr("mc-big")}}


def _who(path, idx):
    return [path.users[i] for i in idx]


def test_each_wallets_moments_keep_the_rules_market_cap_ceiling():
    store = Store(":memory:")
    path, launch = _pumped(store, "mc")
    capped = Trigger("kol", sol=0.95, group="cand", max_mcap=100)
    assert path.mcap_sol(2) < 100 < path.mcap_sol(4)  # mc-1 under the ceiling, mc-2 and mc-big over it
    fired = _first_buys(capped, path, launch, GROUPS)
    assert _who(path, fired) == [addr("mc-1")]  # the buys over the ceiling are no moments: the sniper refuses them
    assert fired[0] == _fire(capped, path, launch, GROUPS)  # the one-a-coin rule fires at the first of them, as before
    floor = Trigger("kol", sol=0.95, group="cand", min_mcap=100)
    assert _who(path, _first_buys(floor, path, launch, GROUPS)) == [addr("mc-2"), addr("mc-big")]
    plain = Trigger("kol", sol=0.95, group="cand")  # no gate: every wallet's first buy, as the qualify lab scores now
    assert _who(path, _first_buys(plain, path, launch, GROUPS)) == [addr("mc-1"), addr("mc-2"), addr("mc-big")]
    store.close()


def test_each_wallets_moments_keep_the_largest_buy_and_the_curve_gates():
    store = Store(":memory:")
    path, launch = _pumped(store, "ms")
    small = Trigger("kol", sol=0.95, group="cand", max_sol=2)  # mc-big's 3 SOL is over the rule's largest buy
    assert _who(path, _first_buys(small, path, launch, GROUPS)) == [addr("mc-1"), addr("mc-2")]
    early = Trigger("kol", sol=0.95, group="cand", max_curve=10)  # mc-1 buys while the curve holds under 10 SOL
    fired = _first_buys(early, path, launch, GROUPS)
    assert _who(path, fired) == [addr("mc-1")] and fired[0] == _fire(early, path, launch, GROUPS)
    store.close()


def test_each_wallets_moments_are_on_the_rules_own_launchpad_only():
    """A copy sniper buys pump.fun's coins (curve and PumpSwap); `_fire` passed over a LaunchLab coin, the per-wallet
    form scored it."""
    store = Store(":memory:")
    path, launch = _pumped(store, "ll", venue=LAUNCHLAB)
    plain = Trigger("kol", sol=0.95, group="cand")
    assert path.venue == LAUNCHLAB and _fire(plain, path, launch, GROUPS) is None
    assert _first_buys(plain, path, launch, GROUPS) == []
    store.close()


def _world(db):
    """24 coins LEAD buys first and LEAD2 five slots later, both followed (the `wallet` group)."""
    st = _store(db)
    try:
        for _ in range(24):
            _coin(st, mate=0.002, later=((LEAD2, 2.0, 0.002, 5),))
    finally:
        st.close()


def test_the_cli_scores_each_followed_wallets_own_buys_with_each(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    _world(db)
    base = ["--db", str(db), "lab", "--trigger", json.dumps(RULE), "--buy", "2", "--per-tx", "0.0045",
            "--copy-position", "+0", "--by-wallet", "--json"]  # fmt: skip
    assert cli.main(base) == 0
    once = json.loads(capsys.readouterr().out)
    assert once["moments"] == 24 and once["each"] is False  # one moment a coin: the first followed wallet in
    assert {w["wallet"]: w["n"] for w in once["wallets"]} == {LEAD: 24}  # LEAD2's buys were never scored
    assert cli.main([*base, "--each"]) == 0
    each = json.loads(capsys.readouterr().out)
    assert each["moments"] == 48 and each["each"] is True  # every followed wallet's own buy, as its sniper copies
    assert {w["wallet"]: w["n"] for w in each["wallets"]} == {LEAD: 24, LEAD2: 24}
    assert cli.main([*base[:-1], "--each"]) == 0  # the text says so too
    assert "each followed wallet's own buys" in capsys.readouterr().out


def test_each_is_refused_where_it_would_drop_a_rule(tmp_path, capsys):
    """`--each` reads wallet rules only: a launch rule beside it would be left out without a word, so it is refused,
    and so is `--each` with no rule named."""
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    _world(db)
    base = ["--db", str(db), "lab", "--buy", "2", "--per-tx", "0.0045", "--each"]
    assert cli.main([*base, "--trigger", json.dumps(RULE), "--trigger", '{"kind": "launch"}']) == 2
    assert "--each" in capsys.readouterr().err
    assert cli.main(base) == 2
    assert "--each" in capsys.readouterr().err

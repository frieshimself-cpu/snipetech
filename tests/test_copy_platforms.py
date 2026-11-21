"""`oktech copylist --platforms pumpfun`: a copy sniper kept to pump.fun's curve (21 Nov 2026).

OKTech tops the wallet's wrapped SOL up to 1.1 times a sniper's buy when it starts, for PumpSwap buys (6 Nov: 0.22
beside 0.2 SOL buys; 20 Nov: 0.055 beside 0.05, and 0.94 moved in for 1 SOL buys), and a buy on pump.fun's curve is
paid in plain SOL only (11 Nov: a copy failed beside 0.47 wrapped SOL). At 5 SOL a copy from the owner's 10 SOL that
top-up leaves about 4.9 plain SOL against the 5.2 a curve copy needs: every copy would fail. The 5 SOL day's paper
scored curve coins (35-100 SOL of market cap), so its snipers can leave PumpSwap out. The default stays both venues."""

import asyncio

import pytest

from snipetech.config import Config
from snipetech.oktech import COPY_PLATFORMS, list_copy_sniper
from snipetech.store import Store

CENTED = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"


def test_a_copy_sniper_sends_both_venues_unless_told_otherwise():
    both = list_copy_sniper(CENTED, "Cented", 0.95, 5.0)
    curve = list_copy_sniper(CENTED, "Cented", 0.95, 5.0, platforms=("pumpfun",))
    assert both.config["platforms"] == list(COPY_PLATFORMS) == ["pumpfun", "pumpswap"]
    assert curve.config["platforms"] == ["pumpfun"]
    words = " ".join(curve.summary)
    assert "platforms pumpfun" in words and "PumpSwap left out" in words and "wrapped SOL" in words
    assert "PumpSwap left out" not in " ".join(both.summary)
    # nothing else about the sniper changes with it
    rest = {k: v for k, v in curve.config.items() if k != "platforms"}
    assert rest == {k: v for k, v in both.config.items() if k != "platforms"}


def test_copylist_hands_the_platforms_to_every_sniper_it_makes():
    from snipetech.service import Service

    store = Store(":memory:")
    svc = Service(Config(), store=store)
    try:
        specs = [f"{CENTED}:Cented:0.95:hold12", "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9:Decu:0.95:hold12"]
        rep = asyncio.run(svc.oktech_copylist(specs, "SNIPER", buy_sol=5.0, bid=0.2, platforms=("pumpfun",)))
        assert [s["config"]["platforms"] for s in rep["snipers"]] == [["pumpfun"], ["pumpfun"]]
        rep = asyncio.run(svc.oktech_copylist(specs[:1], "SNIPER", buy_sol=5.0, bid=0.2))
        assert rep["snipers"][0]["config"]["platforms"] == ["pumpfun", "pumpswap"]
    finally:
        store.close()


def test_copylist_cli_takes_the_platforms_and_refuses_unknown_ones():
    from snipetech.cli import build_parser

    p = build_parser()
    base = ["oktech", "copylist", "--copy", f"{CENTED}:Cented:0.95:hold12"]
    assert p.parse_args([*base, "--platforms", "pumpfun"]).platforms == ("pumpfun",)
    assert p.parse_args([*base, "--platforms", "pumpfun,pumpswap"]).platforms == ("pumpfun", "pumpswap")
    assert p.parse_args(base).platforms is None
    for bad in ("", "raydium", "pumpfun,raydium"):
        with pytest.raises(SystemExit):
            p.parse_args([*base, "--platforms", bad])

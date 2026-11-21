"""`oktech copylist --smart-fees`: OKTech's smart fees on a copy sniper (21 Nov 2026).

Which part of a bid orders a block depends on the validator that builds it, and the copy bots that stand first set
their bid per block (an experienced sniper, 21 Nov ~20:50 UTC; docs/live-recipe.md). OKTech's `globalSmartFees`
"keeps the total the same, it just creates other transactions with rebalanced tip/prio for the more ideal ratios"
(OKTech, the same evening). So it is sent only when asked, it never changes the bid we set, and the summary says it
is on."""

import asyncio

from snipetech.config import Config
from snipetech.oktech import execution_params, list_copy_sniper
from snipetech.store import Store

CENTED = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"


def test_smart_fees_is_sent_only_when_asked_and_keeps_the_bid():
    off = list_copy_sniper(CENTED, "Cented", 0.95, 5.0, priority_fee=0.001, tip=0.199)
    on = list_copy_sniper(CENTED, "Cented", 0.95, 5.0, priority_fee=0.001, tip=0.199, smart_fees=True)
    assert "globalSmartFees" not in off.config["extraParams"]
    assert on.config["extraParams"]["globalSmartFees"] is True
    # the bid we set is the bid sent: smart fees re-splits it per block on OKTech's side, never here
    assert (on.config["priorityFee"], on.config["tipAmount"]) == (off.config["priorityFee"], off.config["tipAmount"])
    assert any("smart fees on" in line for line in on.summary)
    assert not any("smart fees" in line for line in off.summary)
    # the other execution fields are untouched by it
    assert {k: v for k, v in on.config["extraParams"].items() if k != "globalSmartFees"} == off.config["extraParams"]
    assert execution_params(1, 100_000, smart_fees=True) == {**execution_params(1, 100_000), "globalSmartFees": True}


def test_copylist_hands_smart_fees_to_every_sniper_it_makes():
    from snipetech.service import Service

    store = Store(":memory:")
    svc = Service(Config(), store=store)
    try:
        rep = asyncio.run(svc.oktech_copylist([f"{CENTED}:Cented:0.95:hold12"], "SNIPER", buy_sol=5.0, bid=0.2,
                                              smart_fees=True))  # fmt: skip
        (sniper,) = rep["snipers"]
        assert sniper["config"]["extraParams"]["globalSmartFees"] is True
        rep = asyncio.run(svc.oktech_copylist([f"{CENTED}:Cented:0.95:hold12"], "SNIPER", buy_sol=5.0, bid=0.2))
        assert "globalSmartFees" not in rep["snipers"][0]["config"]["extraParams"]
    finally:
        store.close()


def test_copylist_cli_has_the_smart_fees_switch():
    from snipetech.cli import build_parser

    on = build_parser().parse_args(["oktech", "copylist", "--copy", f"{CENTED}:Cented:0.95:hold12", "--smart-fees"])
    off = build_parser().parse_args(["oktech", "copylist", "--copy", f"{CENTED}:Cented:0.95:hold12"])
    assert on.smart_fees is True and off.smart_fees is False

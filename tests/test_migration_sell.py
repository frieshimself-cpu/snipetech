"""OKTech's migration sell, scored by the lab and sent 1:1 (7 Nov).

OKTech's auto-sell can sell part of a position when its coin migrates: `autoSell.migrationSell {sellPercent, delayMs}`,
"on migration to a new pool, sell % after delay (100 ms - 1 min)" (docs/oktech-settings.md). The sniper builder sends
an exit's migration sell exactly as the lab scored it and refuses one the app cannot take. No live migration sell has
fired yet: OKTech accepts settings it then ignores (playbook fact 8), so every sniper that carries one says so."""

import pytest

from snipetech.analysis.lab import Trigger
from snipetech.oktech import autosell, strategy_sniper

LAB = {"stale_secs": 1.0, "stop_loss": 0.3, "max_hold_secs": 300}  # the exit every book sniper runs


def test_autosell_sends_the_migration_sell_as_scored():
    notes: list[str] = []
    a = autosell(LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 1.0}, notes)
    assert a["migrationSell"] == {"enabled": True, "sellPercent": 100.0, "delayMs": 1000}
    assert {k: v for k, v in a.items() if k != "migrationSell"} == autosell(LAB, [])  # nothing else changes
    assert any("migration" in n and "live" in n for n in notes)  # it has never fired on one of our trades
    half = autosell({"max_hold_secs": 300, "migration_sell_frac": 0.5, "migration_sell_delay": 0.1}, [])
    assert half["migrationSell"] == {"enabled": True, "sellPercent": 50.0, "delayMs": 100}  # the app's shortest delay
    longest = autosell(LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 60.0}, [])
    assert longest["migrationSell"]["delayMs"] == 60_000  # and its longest
    assert "migrationSell" not in autosell(LAB, [])
    assert "migrationSell" not in autosell(LAB | {"migration_sell_frac": 0.0, "migration_sell_delay": 1.0}, [])


def test_a_migration_sell_alone_is_still_an_exit():
    a = autosell({"migration_sell_frac": 1.0, "migration_sell_delay": 0.5}, [])
    assert a is not None and a["migrationSell"]["delayMs"] == 500
    assert a["stages"]  # OKTech needs at least one stage when the auto-sell is on


@pytest.mark.parametrize(
    "frac,delay",
    [(1.0, 0.0), (1.0, 0.05), (1.0, 60.5), (1.0, 0.1005), (1.5, 1.0), (1.0, float("nan"))],
)
def test_autosell_refuses_a_migration_sell_oktech_cannot_take(frac, delay):
    # the app takes 100 ms to 1 min, in whole milliseconds, of at most the whole position: anything else would be sent
    # as an exit the lab never scored
    with pytest.raises(ValueError, match="migration sell"):
        autosell(LAB | {"migration_sell_frac": frac, "migration_sell_delay": delay}, [])


def test_the_strategy_sniper_carries_the_migration_sell():
    trig = Trigger("hunt", sol=3.0, window=1.0, dev_max=1.0).__dict__
    ex = LAB | {"migration_sell_frac": 1.0, "migration_sell_delay": 1.0}
    p = strategy_sniper({"id": "m", "label": "t", "buy_sol": 0.1, "exit": ex, "trigger": trig}, 1)
    assert p.config["autoSell"]["migrationSell"] == {"enabled": True, "sellPercent": 100.0, "delayMs": 1000}
    assert any("migration" in n and "live" in n for n in p.notes)
    assert any("migrates" in s for s in p.summary)  # the plan the owner reads before a yes says it sells there

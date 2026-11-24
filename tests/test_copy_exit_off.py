"""A copy sniper's timer without OKTech's copy exit (`holdN-nocopy`), 24 Nov.

Yugi moves half or more of his coins into his own wallets 3 to 6 s after 7 of his last 16 big buys, and OKTech's copy
exit takes the move for a sale: it sold all four of the morning's copies in the slot of his move (docs/live-recipe.md,
the test with the ceiling at 75). On paper a copy sold that early makes a half or less of the 12 s timer's. The timer
alone needs a sniper sent without the copy exit, named for what it is, and replayed the way it ran."""

import pytest

from snipetech.config import Config

YUGI = "AQ46kfYT3hW28Xg5gWHrJkzFSz1oGWBHC3FsTbqgMEco"


def _sniper(exit_name: str):
    from snipetech.oktech import list_copy_sniper

    return list_copy_sniper(YUGI, "Yugi", 2, 0.2, 1, cfg=Config(), exit_name=exit_name)


def test_the_timer_alone_is_named_holdN_nocopy():
    from snipetech.oktech import named_exit

    assert named_exit("hold12-nocopy") == named_exit("hold12s-nocopy") == {"max_hold_secs": 12.0, "copy_exit": False}
    assert named_exit("hold12") == {"max_hold_secs": 12.0}  # the timer beside the copy exit is unchanged
    for bad in ("nocopy", "hold12-copy", "hold12nocopy", "lab-nocopy", "hold60-nocopy-x", "hold0-nocopy",
                "hold601-nocopy", "-nocopy"):  # fmt: skip
        with pytest.raises(ValueError, match="exit"):
            named_exit(bad)


def test_a_holdN_nocopy_sniper_is_sent_without_the_copy_exit_and_says_so():
    """The payload switches OKTech's copy exit off and keeps the timer exactly as holdN sends it; the summary names the
    timer alone and never says the sniper sells when the wallet sells."""
    off, on = _sniper("hold12-nocopy"), _sniper("hold12")
    assert off.config["ctbotCopyExit"]["enabled"] is False
    assert on.config["ctbotCopyExit"]["enabled"] is True
    assert off.config["autoSell"] == on.config["autoSell"]
    assert off.config["autoSell"]["sellAfterSeconds"] == 12
    text = " | ".join(off.summary)
    assert "no copy exit" in text and "when it sells" not in text
    assert "when it sells" in " | ".join(on.summary)


def test_measure_names_and_replays_a_sniper_that_ran_without_the_copy_exit():
    """The measure stage reads the exit back from the sniper's config: a hold12 timer with the copy exit off is
    'hold12-nocopy', not 'hold12', and its shadow replay holds no copy exit (a copy exit of 0 sends none)."""
    from snipetech.analysis.lab import menu_exits
    from snipetech.exits_menu import MenuExit
    from snipetech.measure.exits import exit_that_ran, preset_name

    off, on = _sniper("hold12-nocopy").config, _sniper("hold12").config
    assert preset_name(off) == "hold12-nocopy" and preset_name(on) == "hold12"
    ran = exit_that_ran(off)
    assert ran == ("hold12-nocopy", {"max_hold_secs": 12.0, "copy_exit": False})
    replay = menu_exits(Config(), (MenuExit(ran[0], ran[1], ("timer", "copy_exit")),))["hold12-nocopy"]
    assert replay.copy_exit_pct == 0 and replay.max_hold_secs == 12
    with_copy = exit_that_ran(on)
    assert (
        menu_exits(Config(), (MenuExit(with_copy[0], with_copy[1], ("timer", "copy_exit")),))["hold12"].copy_exit_pct
        > 0
    )

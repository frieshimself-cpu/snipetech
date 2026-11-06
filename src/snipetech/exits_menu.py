"""The exits a copy sniper can run, as one small menu: what the qualify lab scores every wallet on, what the book may
send (only a family the owner verified, [book] verified_exits, and only with [book] exit_mode = "per_wallet"), and what
measure re-plays on every live buy (the exit_shadow table).

Each exit is a snipetech exit (fractions and seconds, as `oktech.autosell` reads them) plus the copy exit every copy
sniper carries ([oktech] copy_exit_threshold_percent, 50 %: sell when the copied wallet sells at least half of its bag
in one transaction). `features` names the OKTech sell rules the exit relies on by the label measure gives a live sell
one of them closed (measure/exits.py REASONS); a family is verified once each of its rules has closed one of our own
live sells, because OKTech accepts settings it then ignores.

The evidence (calibrated replay of the 13 copy fills of 6 Nov, docs/oktech-audit-2026-11-06.md):
- lab: what every book sniper runs. -0.341 SOL on the 13 fills. Its 1 s dead exit fires first (a median 1.9 s after
  the buy), so adding a trailing stop or a take-profit to it changes 0 of the 13 exits.
- hold60: the live hold60 preset (Gfree, Pengu). Its 60 s timer sold 7bDN at 61.2 s. It has no stop-loss, as the live
  preset runs; adding a 30 % one is a later choice, and until then oktech.validate_exit refuses it for the book.
- ride: no dead exit; it rides a winner and sells on a 25 % drop from its best price. +0.155 SOL on DTAU instead of
  -0.022, but 0.12 SOL worse than lab on the other 12 fills: it pays only behind a leader that sells in slices (G2oZ).
  The trailing stop matched our replay on 8 of 11 strategy-sniper sells (5 Nov), never on a copy sniper yet.
A new exit, or a changed one, is a new EXIT_MENU_VERSION: verdicts scored on another version fall back to lab.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

EXIT_MENU_VERSION = "copy-menu-v1"


@dataclass(frozen=True)
class MenuExit:
    name: str  # what the verdict, the book, measure and the shadow table call it
    params: Mapping[str, float]  # snipetech exit parameters (fractions, seconds), read-only
    features: tuple[str, ...]  # the OKTech sell rules it relies on, by the label measure gives a sell they closed
    verified: bool = False  # every feature has closed one of our live sells ([book] verified_exits is the switch)
    family: str = ""  # its family in [book] verified_exits; empty = its name
    note: str = ""  # what it does, in plain words, and what is known about it

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "family", self.family or self.name)


COPY_EXIT_MENU: tuple[MenuExit, ...] = (
    MenuExit(
        "lab",
        {"stale_secs": 1.0, "stop_loss": 0.30, "max_hold_secs": 300},
        ("dead_exit", "stop_loss", "timer", "copy_exit"),
        verified=True,
        note="sells once nothing trades for 1 s, at -30 %, after 300 s, or when the copied wallet sells half its bag; "
        "every book sniper runs it and each of its rules has sold one of our live coins",
    ),
    MenuExit(
        "hold60",
        {"max_hold_secs": 60},
        ("timer", "copy_exit"),
        verified=True,
        note="sells after 60 s, or when the copied wallet sells half its bag; no stop-loss, as the live preset runs "
        "(the 60 s timer sold 7bDN at 61.2 s on 6 Nov)",
    ),
    MenuExit(
        "ride",
        {"trailing_stop": 0.25, "trailing_activation": 0.20, "stop_loss": 0.30, "max_hold_secs": 300},
        ("trailing", "stop_loss", "timer", "copy_exit"),
        verified=False,
        note="rides a winner: once up 20 % it sells on a 25 % drop from its best price; also at -30 %, after 300 s, or "
        "when the copied wallet sells half its bag; no 1 s dead exit. Never run on a copy sniper yet",
    ),
)
MENU: Mapping[str, MenuExit] = MappingProxyType({m.name: m for m in COPY_EXIT_MENU})


def exit_family(name: str | None) -> str:
    """The family of an exit name in [book] verified_exits: the menu's, else the name itself."""
    m = MENU.get(str(name or ""))
    return m.family if m is not None else str(name or "")

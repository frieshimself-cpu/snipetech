"""One candidate an actor: of the wallets one operator buys with, qualify picks one.

11 Nov 2026 (the server's analyst, idea I66): "Wallets buying together pass as separate candidates ... one candidate
a set, so no second sniper on one signal." On 13 Nov three pairs and one set of three were each under a forward test
of their own (CGAZ/Q4J6, EGLz/HVM3, 5Ah8/6gRW/FvHw), every wallet one pass away from a sniper of its own on the same
buys.

The rule, for a wallet every in-sample gate has passed (`OneActor.decide`). Its actor is the wallet and its companions
(analysis/actors.py), joined with the wallets that hold a place now: the ones whose latest verdict is qualified or
under its forward test, and the ones a sniper of the book copies. If another wallet of its actor holds a place, only
one of them is the candidate:

* the wallet a book sniper copies now, whatever else is true;
* else the wallet that already won this choice (`leads`, kept with its numbers), so the candidate does not change hands
  from run to run;
* else, when this wallet is under no forward test of its own, the one that is: the actor is already under test, and a
  second record for the same signal is one more chance to pass by luck (wallets measured in one lab are judged leader
  first, so there the block order decides: `OneActor.order`);
* else, both being under test (the pairs picked before 13 Nov), the signal's leader: the wallet whose buy comes first
  in the block on most of the coins they share, then the better lower bound (actors.leader).

The wallet that yields keeps a verdict of its own with every number the lab measured, as a candidate the book does not
arm, and says "same actor as <wallet>: one signal, one sniper" (`same_actor`). It starts no forward record; one it
already has runs on untouched (nothing is merged, nothing deleted), so it can take the place if the candidate stops
passing. The candidate's own verdict is exactly the one it gets alone. When the candidate gives the place up, the
wallets it was chosen over are due for review at once (`release`), and the queue takes a wallet whose verdict says
"same actor as" with the qualified wallets and the due forward checks (store._DUE_CLASS), not behind the backlog.

Never marked: a wallet a sniper may be copying (the book's reading of OKTech names it, our own record of a sniper for
it is still there, or the reading is unknown and its last verdict was qualified). The book drops a wallet that is no
longer qualified and stops its sniper, and this rule must only ever keep a sniper from being added. Two such wallets
of one actor stay as they are; the book adds no sniper for a second one (book/policy.py).

What each wallet's score record keeps (`actor`): `mates` (its companions), and where it applies `leads`, `over` (the
wallets it was chosen over), `same_as` and `why` (the wallet it yields to), `capped` (its actor hit actors.ACTOR_MAX,
so it may be larger than shown)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..analysis.actors import actor_of, group_actors, leader
from ..analysis.companions import companions
from ..config import QualifyConfig
from ..store import _LATEST_VERDICT, Store

SAME_ACTOR = "same actor as"  # how a verdict that yields says so, first words of the reason
GATE = "actor"  # the gate that set such a verdict's status


@dataclass
class Decision:
    """What the rule found for one wallet: `block` goes into its score record; `same_as` is the wallet it yields to
    (None: it is its actor's candidate, or alone) and `why` says how that wallet was chosen."""

    block: dict[str, Any] = field(default_factory=dict)
    same_as: str | None = None
    why: str = ""


def same_actor(v: dict[str, Any], decision: Decision | None, cfg: QualifyConfig, now: float) -> dict[str, Any]:
    """The verdict of a wallet that yields to another wallet of its actor: `v` with its numbers, as a candidate (the
    book arms only qualified wallets), reviewed as often as a qualified one, the reason last. A verdict a gate retired
    (its own forward check failed) stays as it is; so does one without a decision to yield."""
    if decision is None or not decision.same_as or v.get("status") == "retired":
        return v
    said = (f"{SAME_ACTOR} {decision.same_as}: one signal, one sniper. Not armed while that wallet is the actor's "
            f"candidate ({decision.why})")  # fmt: skip
    return dict(v, status="candidate", gate=GATE, next_review=int(now) + cfg.review_qualified_h * 3600,
                reasons=[*list(v.get("reasons") or []), said])  # fmt: skip


def standing_wallets(store: Store) -> set[str]:
    """The wallets that hold a place now by their latest verdict: qualified, or a candidate under its forward test."""
    rows = store._q(f"SELECT v.wallet FROM ({_LATEST_VERDICT}) v WHERE v.status = 'qualified' "
                    "OR (v.status = 'candidate' AND v.forward IS NOT NULL)")  # fmt: skip
    return {r[0] for r in rows}


def latest_verdict(store: Store, wallet: str) -> tuple[str | None, bool, float | None, int | None]:
    """(status, carries a forward check, lower bound, when) of the wallet's latest verdict; (None, False, None, None)
    without one."""
    rows = store._q("SELECT status, forward IS NOT NULL, lower_sol, computed_at FROM verdicts WHERE wallet=? "
                    "ORDER BY computed_at DESC, id DESC LIMIT 1", (wallet,))  # fmt: skip
    return (rows[0][0], bool(rows[0][1]), rows[0][2], rows[0][3]) if rows else (None, False, None, None)


def actor_block(store: Store, wallet: str) -> dict[str, Any]:
    """The wallet's `actor` record from its last verdict's numbers ({} when it has none)."""
    block = (store.get_score("qualify", wallet) or {}).get("actor")
    return block if isinstance(block, dict) else {}


def actor_links(block: Mapping[str, Any] | None) -> set[str]:
    """The wallets a wallet's `actor` record ties it to: its companions, the wallets it was chosen over and the one it
    yields to. The book groups on these (book/run._actor_links)."""
    b = block or {}
    return {str(x) for x in [*(b.get("mates") or ()), *(b.get("over") or ()), b.get("same_as")] if x}


def _may_pass(row: dict | None, need: int) -> bool:
    """Whether a lab row can pass the tape gate at all: enough copies and a lower bound above zero."""
    if not row or row.get("n") is None or row.get("ev_lower") is None:
        return False
    return int(row["n"]) >= need and float(row["ev_lower"]) > 0


class OneActor:
    """The rule over one qualify run. `store`: verdicts, score records and our record of snipers (the live database);
    `tape`: the trades (the store the lab reads); `since`: the start of the tape the companions are read on;
    `copying`: the wallets (first six characters) a book sniper buys for at the book's last reading of OKTech, None
    when that reading is unknown (book/state.copying_now)."""

    def __init__(self, store: Store, tape: Store, since: int, copying: set[str] | None):
        self.store, self.tape, self.since, self.copying = store, tape, int(since), copying
        self._mates: dict[str, frozenset[str]] = {}  # companions read in this run, the lab's included
        self._pool: set[str] | None = None
        self._snipers: set[str] | None = None
        self._chosen: dict[str, tuple[str, str]] = {}  # wallet -> (its actor's leader in this lab, why)

    def lab_mates(self, wallets: list[str], mates: Mapping[str, Any] | None) -> None:
        """The companions the lab read for the wallets it scored (`default_lab_runner`'s `mates`): taken as they are,
        so the tape is not read a second time. None: the lab kept none (another runner), and they are read when
        needed."""
        if mates is not None:
            self._mates.update({w: frozenset(mates.get(w) or ()) for w in wallets})

    def mates(self, wallet: str) -> frozenset[str]:
        if wallet not in self._mates:
            self._mates[wallet] = companions(self.tape, wallet, self.since)
        return self._mates[wallet]

    def _links(self, wallet: str) -> set[str]:
        """A wallet's ties for the grouping: from its score record when it has one, else read on the tape."""
        block = actor_block(self.store, wallet)
        return actor_links(block) if "mates" in block else set(self.mates(wallet))

    def _sniper_leaders(self) -> set[str]:
        """The wallets our own record holds a sniper for that was not deleted. It counts a stopped sniper too (a stop
        leaves the row), so it only ever keeps a wallet from being marked."""
        if self._snipers is None:
            from ..book.policy import copied_leaders

            self._snipers = {w for rec in self.store.oktech_snipers(active=True) for w in copied_leaders(rec)}
        return self._snipers

    def copied(self, wallet: str) -> bool:
        return self.copying is not None and wallet[:6] in self.copying

    def protected(self, wallet: str, prev: dict | None) -> bool:
        """A sniper may be copying it: marking it could have the book stop that sniper."""
        if self.copying is None:  # nobody knows what the book copies: every wallet it could have armed counts
            return (prev or {}).get("status") == "qualified" or wallet in self._sniper_leaders()
        return self.copied(wallet) or wallet in self._sniper_leaders()

    def pool(self) -> set[str]:
        """The wallets that may hold a place: by their latest verdict when the run began, since then by this run's
        decisions, and the copied ones our record names in full."""
        if self._pool is None:
            self._pool = standing_wallets(self.store) | {w for w in self._sniper_leaders() if self.copied(w)}
        return self._pool

    def holds(self, wallet: str) -> bool:
        """Whether the wallet holds a place right now: copied, or its latest verdict is qualified or under its forward
        test and it does not itself yield to another wallet."""
        if self.copied(wallet):
            return True
        status, checked, _lower, _at = latest_verdict(self.store, wallet)
        if status != "qualified" and not (status == "candidate" and checked):
            return False
        return not actor_block(self.store, wallet).get("same_as")

    def release(self, wallet: str, v: dict[str, Any], now: float) -> int:
        """Before `wallet`'s new verdict `v` is written: when it was its actor's candidate over other wallets and `v`
        no longer holds the place, those wallets are due for review at once (their verdicts as they are, only the
        review date moves: Store.review_now), so one of them can take the place in the next lab instead of a day
        later. Returns how many were made due."""
        over = [r for r in actor_block(self.store, wallet).get("over") or () if r != wallet]
        status = v.get("status")
        holds = status == "qualified" or (status == "candidate" and bool(v.get("forward")) and v.get("gate") != GATE)
        return 0 if holds or not over else self.store.review_now(over, now)

    def order(self, chunk: list[dict], rows: Mapping[str, dict], need: int) -> list[dict]:
        """The wallets of one lab in the order to judge them: as they came, unless the rule needs another. Wallets
        that cannot pass the tape gate first (a candidate that stopped passing gives up its place before the others
        are judged), then the ones that can: each actor's leader ahead of its other wallets (actors.leader on the
        lab's own lower bounds), and a wallet that yielded last time after everything else."""
        hopeful = [c["wallet"] for c in chunk if _may_pass(rows.get(c["wallet"]), need)]
        if not hopeful:
            return chunk
        yielded = {w for w in hopeful if actor_block(self.store, w).get("same_as")}
        rank: dict[str, int] = {}
        if len(hopeful) > 1:
            for g in group_actors(hopeful, {w: self.mates(w) for w in hopeful}):
                if len(g.wallets) > 1:
                    lower = {w: rows[w].get("ev_lower") for w in g.wallets}
                    who, why = leader(self.tape, g.wallets, self.since, lower)
                    rank[who] = -1
                    self._chosen.update({w: (who, why) for w in g.wallets})
        if not yielded and not rank:
            return chunk
        at = {c["wallet"]: i for i, c in enumerate(chunk)}
        can = set(hopeful)
        return sorted(chunk, key=lambda c: (c["wallet"] in can, c["wallet"] in yielded, rank.get(c["wallet"], 0),
                                            at[c["wallet"]]))  # fmt: skip

    def decide(self, wallet: str, lower: float | None, prev: dict | None, picked: bool) -> Decision:
        """The rule for a wallet every in-sample gate passed (see the module's words). `lower`: its lower bound in this
        lab; `prev`: its latest verdict before this run; `picked`: it is under a forward test of its own
        (run.pick_times gave a latest pick)."""
        w = wallet
        mine = self.mates(w)
        block: dict[str, Any] = {"mates": sorted(mine)}
        was = actor_block(self.store, w)
        # it won this choice before and has held its place since: the choice stands (a mark from before a verdict that
        # lost the place says nothing)
        held = (prev or {}).get("status") == "qualified" or bool((prev or {}).get("forward"))
        led = bool(was.get("leads")) and held and (prev or {}).get("status") != "retired"
        if led:  # with the wallets it was chosen over, so they can be told when it gives the place up (`release`)
            block |= {"leads": True} | {k: was[k] for k in ("why", "over") if was.get(k)}
        self.pool().add(w)  # from here on the wallets judged after it see it
        if self.protected(w, prev):
            return Decision(block)
        others = sorted(r for r in self.pool() if r != w)
        if not others:
            return Decision(block)
        g = actor_of(group_actors([w, *others], {w: mine, **{r: self._links(r) for r in others}}), w)
        if g.capped:
            block["capped"] = True
        rivals = [r for r in g.wallets if r != w and self.holds(r)]
        if not rivals:
            return Decision(block)

        def yields(to: str, why: str) -> Decision:
            mine_ = {k: v for k, v in block.items() if k not in ("leads", "over")}
            return Decision(mine_ | {"same_as": to, "why": why}, to, why)

        armed = [r for r in rivals if self.copied(r)]
        if armed:
            return yields(armed[0], "a sniper of the book copies it now")
        marked = [r for r in rivals if actor_block(self.store, r).get("leads")]
        if marked and not led:
            how = actor_block(self.store, marked[0]).get("why")
            return yields(marked[0], "it is this actor's candidate already" + (f": {how}" if how else ""))
        if led and not marked:
            return Decision(block | {"over": sorted({*(block.get("over") or ()), *rivals})})
        lows = {r: latest_verdict(self.store, r)[2] for r in rivals}
        if not picked:
            chosen = self._chosen.get(w)
            if chosen and chosen[0] in rivals:  # measured in one lab: the block order chose
                return yields(*chosen)
            who = rivals[0] if len(rivals) == 1 else leader(self.tape, rivals, self.since, lows)[0]
            since = latest_verdict(self.store, who)[3]
            when = time.strftime("%d %b %H:%M UTC", time.gmtime(since)) if since else "an earlier run"
            return yields(who, f"the actor is already under test with it (its verdict of {when}): no second forward "
                               "record for one signal")  # fmt: skip
        who, why = leader(self.tape, [w, *rivals], self.since, {w: lower, **lows})
        if who == w:
            return Decision(block | {"leads": True, "over": rivals, "why": why})
        return yields(who, why)

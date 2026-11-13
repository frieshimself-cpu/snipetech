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
so it may be larger than shown).

Twins (13 Nov, the scout's idea I109; analysis/twins.py). FLPjyS and i3Sj7y buy the same coins a minute apart and
never in one block, so the companion rule saw two actors: FLPjyS qualified and got its sniper, i3Sj7y was one pass
from a second sniper on the same coins. Two wallets whose lists of coins say they trade one signal are one actor too.
How it is applied, so that it can only ever leave fewer candidates than before:

* the companion rule decides first, on the companions' ties alone, exactly as above; what it decides stands;
* then the wallet is compared with the place-holders that are not of its companions' actor: its twins among them, by
  the tape now (one read of each wallet's buys a run) or by their own record naming it. If one of them holds a place,
  the same four rules choose between them, and the reason says "They are twins: ..." with the pair's numbers;
* a wallet a sniper may copy is never marked here either (`protected` is unchanged), and its record still names its
  twins, so the book adds no sniper for one of them;
* its actor takes twins while it has room (ACTOR_MAX wallets, its companions counted), the wallets that hold a place
  first: in a crowd on one signal it yields to one of them and says `capped`. Nothing is labelled or retired for
  being in a crowd.

The record then also keeps `twins` (how many place-holders the tape shows as its twins now: "this wallet trades with
N others") and `twin_with` (the wallets the twin rule made it one actor with). A twin in `over` or `same_as` is named
in `twin_with` too: `actor_links` leaves those out and `twin_links` gives them, so the companions' grouping (and its
cap) never sees a twin's tie. A wallet without twins has the record it always had.

What can differ from before for a wallet that is nobody's twin itself: when its companion yields to a twin, it no
longer has that companion as a rival, and can hold the companions' place (one of the two holds it, as before)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..analysis.actors import ACTOR_MAX, actor_of, group_actors, leader
from ..analysis.companions import companions
from ..analysis.twins import Pair, first_buys, pair, twins
from ..config import QualifyConfig
from ..store import _LATEST_VERDICT, Store

SAME_ACTOR = "same actor as"  # how a verdict that yields says so, first words of the reason
GATE = "actor"  # the gate that set such a verdict's status


@dataclass
class Decision:
    """What the rule found for one wallet: `block` goes into its score record; `same_as` is the wallet it yields to
    (None: it is its actor's candidate, or alone) and `why` says how that wallet was chosen. `how`: which rule made
    the two one actor, as a sentence, when it was not the companion rule (the twin rule: analysis/twins.py)."""

    block: dict[str, Any] = field(default_factory=dict)
    same_as: str | None = None
    why: str = ""
    how: str = ""


@dataclass
class TwinTies:
    """A wallet's twins among the place-holders (OneActor._twin_ties). `found`: the ones the tape shows now, each with
    what the two have in common; `joined`: the ones it is one actor with (found now or named by their own record, as
    many as the actor has room for); `capped`: some were left out."""

    found: dict[str, Pair] = field(default_factory=dict)
    joined: list[str] = field(default_factory=list)
    capped: bool = False


def same_actor(v: dict[str, Any], decision: Decision | None, cfg: QualifyConfig, now: float) -> dict[str, Any]:
    """The verdict of a wallet that yields to another wallet of its actor: `v` with its numbers, as a candidate (the
    book arms only qualified wallets), reviewed as often as a qualified one, the reason last. A verdict a gate retired
    (its own forward check failed) stays as it is; so does one without a decision to yield."""
    if decision is None or not decision.same_as or v.get("status") == "retired":
        return v
    said = (f"{SAME_ACTOR} {decision.same_as}: one signal, one sniper. {decision.how}Not armed while that wallet is "
            f"the actor's candidate ({decision.why})")  # fmt: skip
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
    yields to. The book groups on these (book/run._actor_links). The wallets the twin rule tied it to are not among
    them (`twin_links`): a record without twins gives exactly what it gave before that rule."""
    b = block or {}
    chosen = {str(x) for x in [*(b.get("over") or ()), b.get("same_as")] if x} - twin_links(b)
    return {str(x) for x in b.get("mates") or () if x} | chosen


def twin_links(block: Mapping[str, Any] | None) -> set[str]:
    """The wallets a wallet's `actor` record ties it to through the twin rule (`twin_with`). Kept apart from
    `actor_links`: those are grouped up to actors.ACTOR_MAX wallets, where one tie more can undo another, and a twin
    must never undo a tie between companions. Twins are tied two by two, on top (book/policy.actor_members)."""
    return {str(x) for x in (block or {}).get("twin_with") or () if x}


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
        self._firsts: dict[str, dict[str, int]] = {}  # first buys read in this run for the twin rule, once a wallet

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
        others = sorted(r for r in self.pool() if r != w)
        if self.protected(w, prev):  # never marked; its record still says which place-holders are its twins
            ties = self._twin_ties(w, [r for r in others if r not in mine], ACTOR_MAX - 1 - len(mine))
            return self._with_twins(Decision(block), was, ties)
        if not others:
            return self._with_twins(Decision(block), was, None)
        g = actor_of(group_actors([w, *others], {w: mine, **{r: self._links(r) for r in others}}), w)
        if g.capped:
            block["capped"] = True
        rivals = [r for r in g.wallets if r != w and self.holds(r)]
        # the companion rule first, exactly as it was before the twin rule: what it decides stands
        first = self._choose(w, rivals, block, led, lower, picked)
        # then the twin rule, among the place-holders that are not its companions: it can only take the place away
        ties = self._twin_ties(w, [r for r in others if r not in g.wallets], ACTOR_MAX - len(g.members))
        more = [r for r in ties.joined if self.holds(r)]
        if first.same_as or not more:
            return self._with_twins(first, was, ties)
        second = self._choose(w, [*rivals, *more], block, led, lower, picked)
        if second.same_as in ties.joined:
            second.how = self._twin_words(w, second.same_as, ties)
        return self._with_twins(second, was, ties)

    def _choose(self, w: str, rivals: list[str], block: dict[str, Any], led: bool, lower: float | None,
                picked: bool) -> Decision:  # fmt: skip
        """Which wallet is the candidate, `w` or one of `rivals` (the wallets of its actor that hold a place): the
        module's four rules, in their order. `block` is not changed."""
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

    def _twin_ties(self, w: str, others: list[str], room: int) -> TwinTies:
        """`w`'s twins among `others` (place-holders that are not its companions): the ones the tape shows now
        (analysis/twins.py: one read of each wallet's buys, kept for the run), and the ones whose own record names
        `w` their twin (an earlier run's finding stands while that record does, as a companion's does). `room`: how
        many wallets `w`'s actor can still take before actors.ACTOR_MAX; the wallets that hold a place first, then by
        address, and the rest are left out (`capped`): `w` joins a crowd one actor's worth at a time."""
        found = twins(self.tape, w, others, self.since, self._firsts)
        kept = [r for r in others if r not in found and w in twin_links(actor_block(self.store, r))]
        ties = sorted([*found, *kept], key=lambda r: (not self.holds(r), r))
        joined = ties[: max(0, room)]
        return TwinTies(found, joined, len(ties) > len(joined))

    def _twin_words(self, w: str, other: str, ties: TwinTies) -> str:
        """How `w` and `other` are twins, as the sentence a verdict's reason carries."""
        if other in ties.found:
            return f"They are twins: {ties.found[other].words()}. "
        for x in (w, other):
            if x not in self._firsts:
                self._firsts[x] = first_buys(self.tape, x, self.since)
        now = pair(self._firsts[w], self._firsts[other])
        return f"They were found to be twins in an earlier run, and that stands: today {now.words()}. "

    def _with_twins(self, d: Decision, was: Mapping[str, Any], ties: TwinTies | None) -> Decision:
        """`d` with what the twin rule found in its record: `twins` (how many place-holders the tape shows as its
        twins now), `twin_with` (the ones it is one actor with: the links `twin_links` reads; a wallet it was chosen
        over as a twin stays among them while it stays in `over`) and `capped` when not every twin was joined. A
        wallet without twins keeps the record it always had."""
        over = set(d.block.get("over") or ())
        joined = set(ties.joined) if ties else set()
        tied = sorted(joined | (twin_links(was) & over))
        extra: dict[str, Any] = {}
        if ties and ties.found:
            extra["twins"] = len(ties.found)
        if tied:
            extra["twin_with"] = tied
        if ties and ties.capped:
            extra["capped"] = True
        if extra:
            d.block = d.block | extra
        return d

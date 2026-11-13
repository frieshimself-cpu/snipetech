"""Launch snipers, found on the tape and told apart from the launchers' own wallets (13 Nov 2026, idea I59, the
scout: "Hunt on known sniper bots: buy a launch once K proven same-block snipers bought it ... Missing: a label kind
for first-slot snipers that a kol trigger takes as its group, launchers' own wallets told apart").

`winners --snipers` runs it. A snipe is a wallet's first buy of a coin, of `min_sol` or more, in the coin's create
block or one of the `max_slots` blocks after it (2: the create block and the next two), on a coin created in the
window, by a wallet that did not create the coin. A wallet with `min_snipes` of them is judged:

- **What it made, at its own place.** `winners`' own measure (`winners._Wallet.close`): the SOL its sales of the
  coin brought less the SOL its buys cost, after the venue's fee and, where the tape has them, each transaction's
  priority fee and tip. One thing is added, because a sniper buys hundreds of launches and many die: a coin it never
  sold out of is a result too, once it is `settle_secs` old, with what it got back less what it paid and the bag
  left at nothing (`unsold`). `winners` calls that an open position and no result, which would let a wallet that
  sells only its winners look like one. A position younger than `settle_secs` is not counted yet (`unsettled`).
  A wallet is listed when the sum is above zero and so is the one-sided bootstrap lower bound of the SOL a snipe.

- **Not a launcher's own wallet.** A launcher's bundle wins on its own launches and stands in every one of their
  create blocks: by its own trades it is the best "sniper" on the tape. It is told apart by what a snipe carries
  (`marks`), each a test this code base already uses elsewhere:
    - `behind_create`: the buy stands in the create block back to back behind the create: in the place of the coin's
      first trade there (the creator's own buy; a buy inside the create transaction has the same place), or directly
      behind the last such buy, with no other transaction between. That is how a launch tool's bundle lands (a Jito
      bundle runs back to back; analysis.launches.classify_insiders reads the same thing from transaction
      fingerprints, which the tape holds for few launches). Checked on the real tape, 13 Nov: three create blocks
      had the create at one place and its two to four bundle wallets in the next ones, the snipers 40 to 320 places
      later; dKMj stood within four places of the create on 2 of its 171 create-block buys of a day, 6Z91 on 15 of
      292. Where the creator bought nothing, the coin's first buyer starts the run: a few outsiders' snipes are then
      marked too, which the share below allows for.
    - `fee_paid`: the buy's fee was paid by the coin's creator or the create's signer (the tape's `payer`).
    - `paid_by_creator`: the coin's creator sent the wallet SOL (a `paid_by_dev` link, events/dev.py; the test of
      qualify.gates.relationship_gate), or is the wallet's first funder (`funders`).
    - `beside_creator`: the wallet is a companion of the coin's creator or of its create's signer
      (analysis.companions: next to it in its block on half its buys), where that launcher has COMPANION_MIN of the
      wallet's snipes or more.
  A wallet with OWN_SHARE_MAX (a fifth) of its snipes marked is a launcher's (`with_launcher_share`). So is one that
  launched a coin itself in the window (`launched`: a crew's wallets take turns as the creator; 13 Nov: 4HXb7i created
  a coin and bought in 17 create blocks of 16 creators that day), and one with ONE_LAUNCHER_MAX (half) of its snipes
  on one launcher's coins: one creator's, or one signer's of the create (a launch tool signs for fresh creators; no
  signer made more than 1.2 % of a day's 46,505 launches on 13 Nov, so a sniper of the whole market is far from half).
  Wallets named as ours, a service, a dev or a bundler are never looked at. The last two marks (links and
  companions) cost reads, so they are looked for only on a wallet that would otherwise be listed.

What it cannot see: a bundle whose wallets land with a gap behind their create and share nothing else with it. And
the numbers are each wallet's own fills. A copy lands behind the wallet, and behind a buy in a create block it
lands a block later at the earliest (lab.create_block_entry): what a rule on these wallets makes is the lab's
question (`{"kind": "kol", "group": "sniper", "known": 2}`), not answered here.

`label_snipers` writes the kind `sniper` (lab.SNIPER_GROUP), the group such a rule reads. One wallet has one label:
a wallet that already carries another kind keeps it and is reported (`kept`); told to (`relabel`), a `cand` label,
which our own screens wrote, is replaced, and a name of any other kind (the owner's lists) never is.

A batch job on the tape copy: one read of the window in ten-minute pieces by time (`Store.trade_pieces`), as
`winners` reads it."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from ..solana.programs import PROTOCOL_WALLETS
from .companions import COMPANION_MIN, companions
from .lab import KOL_MIN_SOL, SNIPER_GROUP, short_wallet
from .stats import bootstrap_mean_bounds
from .winners import _BOUGHT, _COST, _FIRST, _IN, _LAST, _OUT, _SELLS, _SOLD, LAMPORTS, LEFT_OUT_KINDS, _Wallet

MAX_SLOTS = 2  # blocks after the create block a first buy still counts in
MIN_SNIPES = 20  # snipes a wallet needs to be judged (`winners` asks 20 round trips)
SETTLE_SECS = 600.0  # a coin bought this long ago and never sold out of is a result: the bag counts as nothing
OWN_SHARE_MAX = 0.2  # of its snipes carrying a launcher's mark: at this share or more the wallet is a launcher's
ONE_LAUNCHER_MAX = 0.5  # of its snipes on one creator's or one create signer's coins: that launcher's wallet
NOT_SNIPERS = (*LEFT_OUT_KINDS, "dev", "bundler")  # label kinds never looked at: ours, services, named launchers
SOURCE = "snipers"  # the `source` of the labels this writes
RELABELLED = ("cand",)  # the label kinds `relabel` replaces: our own screens' picks, never a name the owner gave
LAUNCHERS_SHOWN = 50
MARK_WORDS = {
    "behind_create": "in the create block back to back behind the create",
    "fee_paid": "with its fee paid by the coin's creator",
    "paid_by_creator": "on a coin whose creator had paid it",
    "beside_creator": "beside the coin's creator or its create's signer, as its companion",
}
NOTE = (
    "Each wallet's own fills at its own place, after the venue's fee and, where the tape has them, its priority "
    "fee and tip; a coin it never sold out of counts with the bag at nothing. Not what a copy of it makes: a copy "
    "lands behind the wallet, and behind a buy in a create block it lands in the block after the create at the "
    "earliest. Whether a rule on these wallets pays is the lab's question (a kol trigger with group sniper)."
)
# a position's own fields, behind `winners`' ten: its launcher's mark, its coin's creator and the create's signer
_MARK, _BY, _SIGNER = 10, 11, 12
_BROKEN = -1  # a create block's run of back-to-back buys has ended: no later buy joins it


class _Sniper(_Wallet):
    """`winners`' wallet (the same result a position), with each result's launcher's mark, its coin's creator and
    the create's signer, whether the coin was sold out of, and the positions too young to judge."""

    __slots__ = ("by", "marks", "signers", "sold", "unsettled")

    def __init__(self) -> None:
        super().__init__()
        self.marks: list[str] = []
        self.by: list[str] = []
        self.signers: list[str] = []
        self.sold: list[bool] = []
        self.unsettled = 0

    def result(self, st: list, sold_out: bool) -> None:
        self.close(st)
        self.marks.append(st[_MARK])
        self.by.append(st[_BY])
        self.signers.append(st[_SIGNER])
        self.sold.append(sold_out)


def _has_table(store, name: str) -> bool:
    return bool(store._q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _read(store, start: int, end: int, launches: dict, max_slots: int, min_lamports: int, left_out: set[str],
          close_frac: float, settle_secs: float) -> tuple[dict[str, _Sniper], int, int, int, bool]:  # fmt: skip
    """One pass over the window in block order: every snipe opens a position, the wallet's later trades in the coin
    fill it, and it ends as a result when the wallet has sold what it bought. Returns the wallets, the trades read,
    the first and last second read, and whether the tape holds the fee columns."""
    fees = store.has_columns("trades", ("payer", "priority", "tip"))
    cols = "slot, pos, ts, mint, user, is_buy, quote, base, fee, " + (
        "priority, tip, payer" if fees else "NULL, NULL, NULL"
    )
    wallets: dict[str, _Sniper] = {}
    run: dict[str, int] = {}  # coin -> the place of the last buy standing back to back behind its create
    read = 0
    first_ts = last_ts = 0
    for rows in store.trade_pieces(start, end, cols, "ORDER BY slot, pos, ev"):
        if not rows:
            continue
        read += len(rows)
        first_ts = first_ts or int(rows[0][2])
        last_ts = int(rows[-1][2])
        for slot, pos, ts, mint, user, is_buy, quote, base, fee, prio, tip, payer in rows:
            la = launches.get(mint)
            if la is None:
                continue  # a coin created before the window: its first buys are not on this read
            c_slot, signer, creator = la
            behind = False
            if slot == c_slot:  # the create block: who stands back to back behind the create
                last = run.get(mint)
                behind = last is None or (last != _BROKEN and pos - last <= 1)
                run[mint] = pos if behind else _BROKEN
            if user == signer or user == creator:
                continue  # a coin's creator is no sniper of its coin
            w = wallets.get(user)
            if w is None:
                if not is_buy or slot > c_slot + max_slots or quote < min_lamports or user in left_out:
                    continue
                w = wallets[user] = _Sniper()
            had = mint in w.open
            st = w.open.get(mint)
            if had and st is None:
                continue  # the coin's snipe is done: a later round trip in it is no snipe
            cost = 0
            tx = (slot << 16) | pos  # a block and a place in it are one transaction: its fee is paid once
            if prio is not None:
                w.fee_known += 1
                if tx != w.tx:
                    cost = int(prio) + int(tip or 0)
            w.tx = tx
            w.trades += 1
            if is_buy:
                if st is None:
                    if slot > c_slot + max_slots:
                        w.open[mint] = None  # its first buy of the coin came later: no snipe
                        continue
                    if quote < min_lamports:
                        continue  # dust, not a buy a rule follows: a larger one in these blocks still is its snipe
                    mark = "behind_create" if behind else "fee_paid" if payer and payer in (signer, creator) else ""
                    st = w.open[mint] = [0, 0, 0, 0, 0, ts, ts, 0, False, slot - c_slot, mark, creator or signer,
                                         signer or creator]  # fmt: skip
                st[_BOUGHT] += base
                st[_OUT] += quote + fee
                st[_COST] += cost
                continue
            if st is None:
                w.open[mint] = None  # it sells a coin it never bought on this read: handed to it, no snipe
                continue
            st[_SOLD] += base
            st[_IN] += quote - fee
            st[_COST] += cost
            st[_SELLS] += 1
            st[_LAST] = ts
            if st[_SOLD] >= st[_BOUGHT]:
                w.result(st, True)
                w.open[mint] = None
    for w in wallets.values():
        for st in w.open.values():
            if st is None:
                continue
            if st[_BOUGHT] > 0 and st[_SOLD] >= close_frac * st[_BOUGHT]:
                w.result(st, True)  # out but for dust
            elif st[_FIRST] <= last_ts - settle_secs:
                w.result(st, False)  # never sold out of: what came back less what it paid, the bag at nothing
            else:
                w.unsettled += 1
    return wallets, read, first_ts, last_ts, bool(fees)


def _paid_by(links, tables: tuple[bool, bool], wallet: str) -> set[str]:
    """The wallets that paid `wallet` as a coin's creator (`paid_by_dev` links) and its first funder. `tables`:
    whether `links` holds the links and the funders at all (a file from before them does not)."""
    out: set[str] = set()
    if tables[0]:
        out |= {src for (src,) in links._q("SELECT src FROM edges WHERE dst=? AND kind='paid_by_dev'", (wallet,))}
    if tables[1]:
        out |= {f for (f,) in links._q("SELECT funder FROM funders WHERE wallet=?", (wallet,)) if f}
    return out


def _is_launcher(r: dict[str, Any], marks: list[str]) -> str:
    """Why a judged wallet is a launcher's own, in plain words; "" when nothing says so. `marks`: the launcher's mark
    of each of its snipes ("" for none), written into the row as counts (`marks`, `with_launcher`, its share)."""
    got = Counter(m for m in marks if m)
    r["marks"] = {m: got[m] for m in MARK_WORDS if got[m]}
    r["with_launcher"] = sum(got.values())
    r["with_launcher_share"] = round(r["with_launcher"] / r["snipes"], 3)
    why = []
    if r["launched"]:
        why.append(f"launched {r['launched']} coin{'s' if r['launched'] > 1 else ''} itself in the window")
    if r["with_launcher_share"] >= OWN_SHARE_MAX:
        parts = ", ".join(f"{n} {MARK_WORDS[m]}" for m, n in r["marks"].items())
        why.append(f"{r['with_launcher']} of its {r['snipes']} snipes are a launcher's own buys ({parts})")
    if r["top_launcher_share"] >= ONE_LAUNCHER_MAX:
        why.append(f"{r['top_launcher_share']:.0%} of its snipes are one launcher's coins (one creator's, or one "
                   "signer's of the create)")  # fmt: skip
    return "; ".join(why)


def launch_snipers(store, now: float, *, hours: float = 72, max_slots: int = MAX_SLOTS, min_snipes: int = MIN_SNIPES,
                   min_sol: float = KOL_MIN_SOL, top: int = 200, labels: dict[str, tuple[str, str]] | None = None,
                   links=None, proven: bool = True, close_frac: float = 0.9, settle_secs: float = SETTLE_SECS,
                   confidence: float = 0.8, samples: int = 1000) -> dict[str, Any]:  # fmt: skip
    """The launch snipers of the newest `hours` of tape, the most SOL made first: `wallets` (the `top` wallets that
    made SOL on their snipes with a lower bound above zero and are no launcher's own), `launchers` (the judged
    wallets told apart as a launcher's own, the busiest LAUNCHERS_SHOWN, each with `why`), and what the read covered.
    `labels`: address -> (kind, name) from the live database; `links`: the database whose `paid_by_dev` links and
    funders are read (default: `store`). `proven` False lists every judged wallet that is no launcher's, losing or
    not (`label_snipers` still labels none that did not pay). The module's text says what a snipe, a result and each
    mark are."""
    labels = labels or {}
    links = store if links is None else links
    start, end = int(now - hours * 3600), int(now)
    left_out = {a for a, (k, _) in labels.items() if k in NOT_SNIPERS} | set(PROTOCOL_WALLETS)
    launches: dict[str, tuple[int, str, str]] = {}
    launched: Counter = Counter()  # wallet -> coins it created in the window
    for mint, slot, signer, creator in store._q("SELECT mint, slot, user, creator FROM launches WHERE ts>=? AND ts<=?",
                                                 (start, end)):  # fmt: skip
        launches[mint] = (int(slot), signer, creator)
        launched.update({a for a in (signer, creator) if a})
    floor = round(min_sol * LAMPORTS)
    wallets, read, first_ts, last_ts, fees = _read(
        store, start, end, launches, int(max_slots), floor, left_out, close_frac, settle_secs
    )
    days = max(60.0, float((last_ts or end) - (first_ts or start))) / 86400  # what the tape read covers
    mates: dict[str, frozenset] = {}  # creator or signer -> its companions, asked once
    tables = (_has_table(links, "edges"), _has_table(links, "funders"))
    listed: list[dict[str, Any]] = []
    theirs: list[dict[str, Any]] = []
    judged = losing = 0
    for user, w in wallets.items():
        n = len(w.pnl)
        if n < max(1, int(min_snipes)):
            continue
        judged += 1
        total = sum(w.pnl)
        by = Counter(w.by)
        # coins by launcher: each coin under its creator and, where another wallet signed the create, under that too
        of = Counter(w.by) + Counter(s for s, c in zip(w.signers, w.by, strict=True) if s != c)
        marks = list(w.marks)
        lab = labels.get(user)
        r: dict[str, Any] = {
            "wallet": user, "name": lab[1] if lab else "", "label_kind": lab[0] if lab else "",
            "snipes": n, "snipes_per_day": round(n / days, 1), "net_sol": round(total, 4),
            "sol_per_day": round(total / days, 3), "mean_sol": round(total / n, 5), "lower_sol": None,
            "win_rate": round(sum(1 for x in w.pnl if x > 0) / n, 3),
            "sold_sol": round(sum(x for x, sold in zip(w.pnl, w.sold, strict=True) if sold), 4),
            "unsold": sum(1 for sold in w.sold if not sold), "unsettled": w.unsettled,
            "create_block": int(w.create), "create_block_share": round(w.create / n, 3),
            "median_entry_slots": median(w.entry), "median_buy_sol": round(median(w.buy), 4),
            "median_hold_secs": round(median(w.hold), 1),
            "creators": len(by), "top_launcher_share": round(max(of.values()) / n, 3),
            "launched": int(launched.get(user, 0)),
            "fees_sol": round(w.fees / LAMPORTS, 6), "fee_known_share": round(w.fee_known / max(1, w.trades), 3),
        }  # fmt: skip
        why = _is_launcher(r, marks)
        if not why and total > 0:  # it would be listed: the two tests that cost reads
            paid = _paid_by(links, tables, user)
            for k, (creator, signer) in enumerate(zip(w.by, w.signers, strict=True)):
                if marks[k]:
                    continue
                if creator in paid or signer in paid:
                    marks[k] = "paid_by_creator"
                    continue
                for launcher in dict.fromkeys((creator, signer)):
                    if of[launcher] < COMPANION_MIN:
                        continue  # too few of its launches to say who buys beside it
                    if launcher not in mates:
                        mates[launcher] = companions(store, launcher, start)
                    if user in mates[launcher]:
                        marks[k] = "beside_creator"
            why = _is_launcher(r, marks)
        if why:
            theirs.append(r | {"why": why})
            continue
        if total > 0:
            r["lower_sol"] = round(bootstrap_mean_bounds(w.pnl, confidence, samples)[0], 5)
        if total <= 0 or r["lower_sol"] <= 0:
            losing += 1
            if proven:
                continue
        listed.append(r)
    listed.sort(key=lambda r: -r["net_sol"])
    theirs.sort(key=lambda r: (-r["snipes"], -r["net_sol"]))
    return {
        "hours": hours, "days": round(days, 4), "max_slots": int(max_slots), "min_snipes": int(min_snipes),
        "min_sol": min_sol, "trades_read": read, "launches": len(launches), "wallets_seen": len(wallets),
        "judged": judged, "losing": losing, "launchers_found": len(theirs), "listed": len(listed),
        "fees_on_tape": fees, "note": NOTE, "wallets": listed[:top], "launchers": theirs[:LAUNCHERS_SHOWN],
    }  # fmt: skip


def sniper_name(r: dict[str, Any]) -> str:
    """The label a found sniper gets: its short address and what it is."""
    return (f"{short_wallet(r['wallet'])} launch sniper, {r['snipes']} snipes {r['net_sol']:+.1f} SOL, "
            f"{r['create_block_share']:.0%} in the create block")  # fmt: skip


def label_snipers(store, rows: list[dict[str, Any]], n: int, relabel: bool = False) -> dict[str, list]:
    """Label the best `n` of `rows` (`launch_snipers`' wallets, best first) with the kind `sniper`, in the live
    database. A wallet that carries a label of another kind keeps it and is named under `kept` (it is then not in
    the group; `label ADDRESS NAME --kind sniper` moves one by hand). With `relabel` a `cand` label is replaced; a
    name of any other kind never is. A wallet already labelled `sniper` is written again with its new numbers.
    Nothing is written with `n` 0."""
    out: dict[str, list] = {"labelled": [], "kept": []}
    if n <= 0:
        return out
    kinds = store.label_kinds()
    for r in rows:
        if len(out["labelled"]) >= n:
            break
        if not (r["net_sol"] > 0 and (r.get("lower_sol") or 0) > 0):
            continue  # listed with `proven` off: it did not pay, and is no sniper to follow
        kind, name = kinds.get(r["wallet"], (None, ""))
        if kind not in (None, SNIPER_GROUP) and not (relabel and kind in RELABELLED):
            out["kept"].append({"wallet": r["wallet"], "kind": kind, "name": name})
            continue
        store.set_label(r["wallet"], sniper_name(r), SNIPER_GROUP, SOURCE)
        out["labelled"].append(r["wallet"])
    return out


def render(o: dict[str, Any]) -> str:
    """`winners --snipers` in words: the snipers, then the wallets told apart as a launcher's own."""
    head = (
        f"launch snipers: the newest {o['hours']:g} h asked for, {o['days']:.2f} days of tape read "
        f"({o['trades_read']:,} trades, {o['launches']:,} launches)"
    )
    judged = (
        f"  a snipe: a wallet's first buy of a coin, {o['min_sol']:g} SOL or more, in the create block or the "
        f"{o['max_slots']} block(s) after it. {o['judged']:,} wallets made {o['min_snipes']} or more: "
        f"{o['listed']:,} listed, {o['losing']:,} not paying, {o['launchers_found']:,} a launcher's own"
    )
    out = [head, judged]
    if not o.get("fees_on_tape"):
        out.append(
            "  this tape file has no priority fees or tips: the SOL made is before what the wallets paid to land"
        )
    for r in o["wallets"]:
        lower = "-" if r["lower_sol"] is None else f"{r['lower_sol']:+.4f}"
        named = f"  [{r['label_kind']}: {r['name']}]" if r["label_kind"] else ""
        out.append(
            f"{r['wallet']}  {r['net_sol']:+.2f} SOL  {r['snipes']} snipes  win {r['win_rate']:.0%}  "
            f"lower/snipe {lower}  in the create block {r['create_block_share']:.0%}  "
            f"buy {r['median_buy_sol']:g} SOL  hold {r['median_hold_secs']:g} s  unsold {r['unsold']}  "
            f"{r['creators']} creators{named}"
        )
    if not o["wallets"]:
        out.append("  no wallet made SOL on enough snipes")
    if o["launchers"]:
        out.append(
            f"a launcher's own wallets, not listed (the busiest {len(o['launchers'])} of {o['launchers_found']}):"
        )
        out += [f"  {r['wallet']}  {r['snipes']} snipes  {r['net_sol']:+.2f} SOL  {r['why']}" for r in o["launchers"]]
    for key, what in (("labelled", "labelled sniper"), ("kept", "kept the label they had (not in the group)")):
        if o.get(key):
            names = [w if isinstance(w, str) else f"{w['wallet']} [{w['kind']}: {w['name']}]" for w in o[key]]
            out.append(f"{what}: " + ", ".join(names))
    out.append("  " + o["note"])
    return "\n".join(out)

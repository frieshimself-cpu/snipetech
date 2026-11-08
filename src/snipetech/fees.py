"""Fees per task: what each planned sniper bids, from its kind and the evidence.

Every OKTech sniper pays four numbers: the buy priority fee, the buy tip, the sell tip and the sell priority fee. This
module gives each planned sniper its four from its kind (a copy of a given leader, a hunt, a launch sniper, a MIG
sniper, a probe) and the evidence on `fills`, inside the owner's bounds (`[fees] bounds`, config.FeesConfig: per kind,
per number, a min and a max; the strategist may cap them with `[fees] tune`, never widen them and never raise a
minimum, since a raised minimum would be a raised bid with no fills behind it).

The rule of evidence
--------------------
A bid (a tip) is raised above the minimum only where 10+ fills at the higher bid landed measurably earlier than 10+
fills at the lower one: lower `trades_behind` (or, where that is not on record, lower `price_gap_pct`), a one-sided
test at the 80 % level (Welch's z of the two means at or above 0.8416). Otherwise the minimum. The 6 Nov fee test is
the reason the burden sits where it does: paying 20 times the fee (0.02 + 0.02) landed no earlier and cost 6 times as
much a round trip. Among several higher bids that pass, the cheapest is chosen; a passing bid above the owner's max is
capped at the max.

The two priority fees are never raised on evidence: OKTech charges the buy priority fee again on the landed sell of
every exit (7 of 7 fee-test sells), and the sell priority fee is what each of a sell's failed twins pays (OKTech signs
every exit on up to three nonce accounts; one lands, the others fail with pump error 6023 and pay priority + base fee,
while a failed send's tip is refunded). So the priorities stay at the kind's minimum and the bids are carried by the
tips. The `floor` a caller passes (the book's arm, a verdict's bribe) raises a number's lower end inside the bounds,
never past the max. The owner's `[oktech] sell_priority_sol` is the floor of every kind's sell priority (the split's
number); `[oktech] sell_tip_sol` is the floor of the copy kind's sell tip only, because it priced the live copies'
sells and nothing else: a hunt, launch, MIG or probe sniper bids its own kind's minimum (0.001, what it bid before
the split) unless its builder asks for more.

Evidence is per kind, and for copies per leader: a leader's fills never price another leader's sniper; a copy fill is
never evidence for a launch kind. Hunt, launch and MIG fills are told apart by the snipers alive at the fill
(`kind_of_payload`), when the caller gives them; a fill no sniper of one kind explains is nobody's evidence.

Code only: nothing here trades, starts a sniper or changes a setting; the numbers go into a planned sniper's config
and its notes say which fee it got and why."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from .config import FEE_KINDS, FEE_NUMBERS, Config

MIN_FILLS = 10  # fills at each bid before the higher one can be chosen
Z_80 = 0.8416  # one-sided 80 % level
BAND_LAMPORTS = 500_000  # tips within 0.0005 SOL are one bid
LANDING_KEYS = ("trades_behind", "price_gap_pct")  # lower is earlier; the first one on record decides
BIDS = ("buy_tip", "sell_tip")  # the numbers evidence may raise
RULE = ("a bid is raised above the minimum only where 10+ fills at the higher bid landed measurably earlier "
        "(trades_behind, else price_gap_pct, lower; one-sided at the 80 % level) than 10+ at the lower; otherwise "
        "the minimum. Priorities are never raised: the buy priority is charged again on every exit, the sell priority "
        "on every failed twin.")  # fmt: skip


@dataclass(frozen=True)
class FeeChoice:
    kind: str
    leader: str | None
    buy_priority: float
    buy_tip: float
    sell_tip: float
    sell_priority: float
    why: dict[str, str] = field(default_factory=dict)  # per number, in plain words
    evidence: dict[str, Any] = field(default_factory=dict)  # per bid: the bands compared

    def task(self) -> str:
        return f"copy of {self.leader[:6]}" if self.kind == "copy" and self.leader else self.kind

    def autosell_fees(self) -> dict[str, float]:
        return {"sell_tip_sol": self.sell_tip, "sell_priority_sol": self.sell_priority}

    def note(self) -> str:
        """One line for a planned sniper's notes: the four numbers and why each tip is where it is."""
        return (f"fees ({self.task()}): buy {self.buy_priority:g} priority + {self.buy_tip:g} tip, sell {self.sell_tip:g} tip "
                f"+ {self.sell_priority:g} priority; buy tip: {self.why.get('buy_tip', '?')}; sell tip: "
                f"{self.why.get('sell_tip', '?')}")  # fmt: skip

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "leader": self.leader, **{n: getattr(self, n) for n in FEE_NUMBERS},
                "why": dict(self.why), "evidence": dict(self.evidence)}  # fmt: skip


def kind_of_payload(payload: dict | None) -> str | None:
    """The fee kind of an OKTech sniper from the config it was created with: ctbot -> copy, hunt, mig, else launch
    (normal mode on launches: dev, replica, whale). None without a config. A probe is told by its local kind."""
    if not isinstance(payload, dict):
        return None
    mode = str(payload.get("mode") or "normal").lower()
    if mode == "ctbot":
        return "copy"
    if mode in ("hunt", "mig"):
        return mode
    return "launch"


def effective_bounds(cfg: Config, kind: str) -> dict[str, tuple[float, float]]:
    """(min, max) per number for `kind`: the owner's outer bounds, capped by the strategist's tune max, never widened.
    The minimum is always the owner's: a tune's min is ignored, since a raised minimum would raise a bid on no
    evidence. An unknown kind takes the launch bounds."""
    kind = kind if kind in FEE_KINDS else "launch"
    outer = cfg.fees.bounds.get(kind) or {}
    tune = cfg.fees.tune.get(kind) or {}
    out: dict[str, tuple[float, float]] = {}
    for n in FEE_NUMBERS:
        o, t = outer.get(n) or {}, tune.get(n) or {}
        lo, hi = float(o.get("min", 0.0)), float(o.get("max", 0.0))
        hi2 = min(hi, float(t.get("max", hi))) if "max" in t else hi
        if hi2 < lo - 1e-12:  # a cap under the owner's minimum is ignored
            hi2 = hi
        out[n] = (lo, hi2)
    return out


def _lamports(sol: float) -> int:
    return round(float(sol) * 1e9)


def _band_of(lamports: int) -> int:
    return round(lamports / BAND_LAMPORTS)


def landed_earlier(low: list[dict], high: list[dict]) -> dict[str, Any]:
    """Whether `high` landed measurably earlier than `low`: on the first landing key both have 10+ values for, Welch's
    z of mean(low) - mean(high) at or above Z_80. Returns the test's parts (metric, n per side, means, z, earlier)."""
    for key in LANDING_KEYS:
        a = [float(r[key]) for r in low if r.get(key) is not None and math.isfinite(float(r[key]))]
        b = [float(r[key]) for r in high if r.get(key) is not None and math.isfinite(float(r[key]))]
        if len(a) < MIN_FILLS or len(b) < MIN_FILLS:
            continue
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        va, vb = statistics.pvariance(a), statistics.pvariance(b)
        se = math.sqrt(va / len(a) + vb / len(b))
        z = (ma - mb) / se if se > 0 else (math.inf if ma > mb else 0.0)
        return {"metric": key, "n_low": len(a), "n_high": len(b), "mean_low": round(ma, 3), "mean_high": round(mb, 3),
                "z": None if math.isinf(z) else round(z, 3), "earlier": z >= Z_80}  # fmt: skip
    return {"metric": None, "n_low": len(low), "n_high": len(high), "earlier": False}


def _bands(rows: list[dict], tip_key: str = "tip_lamports") -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        if r.get(tip_key) is None:
            continue
        out.setdefault(_band_of(int(r[tip_key])), []).append(r)
    return out


def _band_sol(rows: list[dict]) -> float:
    return round(statistics.median(int(r["tip_lamports"]) for r in rows) / 1e9, 6)


def _pick_bid(rows: list[dict], lo: float, hi: float, what: str) -> tuple[float, str, dict[str, Any]]:
    """The bid for one number from the fills that carry it: the minimum `lo` unless a higher band passes the rule.
    Returns (value, why, evidence)."""
    bands = _bands(rows)
    if not rows or not bands:
        return lo, f"the minimum: no fills on record for this {what}", {"bands": {}}
    base_band = _band_of(_lamports(lo))
    low = [r for b, rs in bands.items() if b <= base_band for r in rs]
    higher = sorted((b, rs) for b, rs in bands.items() if b > base_band)
    ev: dict[str, Any] = {"bands": {f"{_band_sol(rs):g}": len(rs) for _, rs in sorted(bands.items())}}
    if not higher:
        return lo, f"the minimum: all {len(low)} fills on record paid it, none a higher tip to compare", ev
    if len(low) < MIN_FILLS:
        n_hi = sum(len(rs) for _, rs in higher)
        return (
            lo,
            (f"the minimum: {len(low)} fills at it, {MIN_FILLS} needed to compare with the {n_hi} at a higher tip"),
            ev,
        )
    tests = []
    for _, rs in higher:
        t = landed_earlier(low, rs) | {"tip": _band_sol(rs)}
        tests.append(t)
        ev[f"{t['tip']:g}"] = t
        if len(rs) < MIN_FILLS:
            continue
        if t["earlier"]:
            tip = min(t["tip"], hi)
            words = (f"{t['n_high']} fills at {t['tip']:g} landed earlier than {t['n_low']} at {lo:g} "
                     f"({t['metric']} {t['mean_high']} vs {t['mean_low']}, z {t['z']})")  # fmt: skip
            if tip < t["tip"]:
                words += f"; capped at the owner's max {hi:g}"
            return tip, words, ev
    thin = [t for t in tests if t["n_high"] < MIN_FILLS]
    tested = [t for t in tests if t["n_high"] >= MIN_FILLS]
    if tested:
        t = tested[0]
        return lo, (f"the minimum: {t['n_high']} fills at {t['tip']:g} landed no earlier than {t['n_low']} at {lo:g} "
                    f"({t['metric']} {t['mean_high']} vs {t['mean_low']}, z {t['z']})"), ev  # fmt: skip
    t = thin[0]
    return lo, f"the minimum: {t['n_high']} fills at {t['tip']:g}, {MIN_FILLS} needed", ev


def _select(
    fills: list[dict] | None, kind: str, leader: str | None, kinds_of: dict[str, str] | None
) -> tuple[list, list]:
    """(buys, sells) of this kind (and leader) from the fills. A copy fill carries the wallet it copied; another kind's
    fill is told by `kinds_of` (sig -> kind, from the snipers alive at it); a sell takes its buy's kind."""
    rows = fills or []
    buys = [r for r in rows if r.get("side") == "buy"]
    sells = [r for r in rows if r.get("side") == "sell"]
    if kind == "copy":
        mine = [b for b in buys if b.get("wallet_copied") and (leader is None or b["wallet_copied"] == leader)]
    else:
        mine = [b for b in buys if not b.get("wallet_copied") and (kinds_of or {}).get(b.get("sig") or "") == kind]
    sigs = {b["sig"] for b in mine if b.get("sig")}
    return mine, [s for s in sells if s.get("buy_sig") in sigs]


def choose(cfg: Config, kind: str, leader: str | None = None, fills: list[dict] | None = None,
           floor: dict[str, float] | None = None, kinds_of: dict[str, str] | None = None) -> FeeChoice:  # fmt: skip
    """The four fees for a planned sniper of `kind` (for a copy, of `leader`), from `fills` (the measure stage's rows:
    buys with tip_lamports, trades_behind, price_gap_pct, wallet_copied; sells with buy_sig) inside the bounds. With
    no fills, the minimums. `floor` raises a number's lower end (the book's arm), never past the max."""
    kind = kind if kind in FEE_KINDS else "launch"
    bounds = effective_bounds(cfg, kind)
    # the owner's [oktech] sell priority is the base every sell starts from and the [oktech] sell tip the base of a
    # copy's (the live copies' sell bid; the other kinds keep their own minimum, 0.001, what they bid before the
    # split); a caller's floor (the book's arm, a verdict's recommended bribe) is the base of its buy bid: an explicit
    # ask, sent as given, said so in the words when it is outside the owner's bounds; evidence may still raise a tip
    # above it, up to the owner's max
    base = {"sell_priority": cfg.oktech.sell_priority_sol}
    if kind == "copy":
        base["sell_tip"] = cfg.oktech.sell_tip_sol
    floor = base | dict(floor or {})
    lo_of: dict[str, float] = {}
    hi_of: dict[str, float] = {}
    floored: dict[str, str] = {}
    for n in FEE_NUMBERS:
        lo, hi = bounds[n]
        f = floor.get(n)
        if f is not None and abs(float(f) - lo) > 1e-12:
            lo = float(f)
            floored[n] = f" (floor {lo:g} asked for by the caller" + (f", above the owner's max {hi:g})" if lo > hi + 1e-12 else
                                                                      f", under the owner's min {bounds[n][0]:g})" if lo < bounds[n][0] - 1e-12 else ")")  # fmt: skip
            hi = max(hi, lo)
        lo_of[n], hi_of[n] = lo, hi
    buys, sells = _select(fills, kind, leader, kinds_of)
    values: dict[str, float] = {}
    why: dict[str, str] = {}
    evidence: dict[str, Any] = {}
    for n in FEE_NUMBERS:
        lo, hi = lo_of[n], hi_of[n]
        if n in BIDS:
            rows = buys if n == "buy_tip" else sells
            v, words, ev = _pick_bid(rows, lo, hi, "buy" if n == "buy_tip" else "sell")
            evidence[n] = ev
        else:
            v, words = lo, ("the minimum: charged again on every exit, never raised" if n == "buy_priority"
                            else "the minimum: what each failed twin pays, never raised")  # fmt: skip
        words += floored.get(n, "")
        values[n], why[n] = round(v, 6), words
    return FeeChoice(kind, leader, values["buy_priority"], values["buy_tip"], values["sell_tip"], values["sell_priority"],
                     why, evidence)  # fmt: skip


def kinds_of_fills(fills: list[dict], snipers: list[dict]) -> dict[str, str]:
    """sig -> fee kind for the non-copy buys: the kind of the non-copy snipers alive at the buy (created_at ..
    removed_at, from the local oktech_snipers table) when they agree; a buy two kinds could explain is left out."""
    live = []
    for r in snipers:
        k = "probe" if r.get("kind") == "probe" else kind_of_payload(r.get("payload"))
        if k and k != "copy":
            live.append((float(r.get("created_at") or 0), r.get("removed_at"), k))
    out: dict[str, str] = {}
    for b in fills:
        if b.get("side") != "buy" or b.get("wallet_copied") or b.get("ts") is None or not b.get("sig"):
            continue
        ts = float(b["ts"])
        kinds = {k for born, gone, k in live if born - 60 <= ts and (gone is None or ts <= float(gone) + 60)}
        if len(kinds) == 1:
            out[b["sig"]] = kinds.pop()
    return out


def evidence_fills(store, wallet: str | None, now: float, days: float = 30.0) -> list[dict]:
    """The measure stage's fills of the last `days` for the policy: buys and sells of the operator wallet."""
    since = int(now - days * 86400)
    rows = store.fills(wallet=wallet or None, since_ts=since, limit=100_000)
    return [r for r in rows if r.get("side") in ("buy", "sell")]


def explain(cfg: Config, fills: list[dict] | None = None, leaders: list[str] | tuple[str, ...] = (),
            kinds_of: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The policy's current choice per kind, per copied leader, with why, the bounds and the rule: what
    `read_fee_policy` returns to the operator and the analyst."""
    kinds = {k: choose(cfg, k, fills=fills, kinds_of=kinds_of).as_dict() for k in FEE_KINDS}
    per_leader = [choose(cfg, "copy", leader=w, fills=fills).as_dict() for w in dict.fromkeys(leaders) if w]
    return {"rule": RULE, "kinds": kinds, "copy_leaders": per_leader,
            "bounds": {k: {n: list(b) for n, b in effective_bounds(cfg, k).items()} for k in FEE_KINDS},
            "owner_bounds": {k: {n: [float(r["min"]), float(r["max"])] for n, r in (cfg.fees.bounds.get(k) or {}).items()}
                             for k in FEE_KINDS},
            "base_sell": {"sell_tip_sol": cfg.oktech.sell_tip_sol, "sell_priority_sol": cfg.oktech.sell_priority_sol},
            "fills": len(fills or [])}  # fmt: skip

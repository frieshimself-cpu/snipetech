"""The big-deployer funding study as a lab rule (26 Nov 2026): a launch snipe on coins whose creator was paid, before
the create, by the creator of a coin that had already passed a market-cap bar. The owner's "track funds moving through
big deployer wallets and snipe" (24-25 Nov); its first scripts lived in a scratch folder and went with the container
on 26 Nov, so it comes back as a knob of the lab, which scores it at the launch landing with every exit and cost the
other launch rules have.

No hindsight (the refuters' warning on the first plan): a creator counts as big only from the moment one of his coins
passed the bar (the PumpSwap month, `history amm-peaks`), and both that moment and the payment (a paid_by_dev link,
events/dev.py) come before the create, by a second at least."""

import json
from pathlib import Path

import pytest

from snipetech.analysis import amm_peaks as ap
from snipetech.analysis import funded as fu
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, coin_keys, collect, parse_trigger, research_only_why
from snipetech.config import Config
from snipetech.models import Launch
from snipetech.oktech import strategy_sniper
from snipetech.store import Store

from .synth import addr, pump_launch

T0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC
H = 3600
SLOT0 = 300_000_000
BIG = 1_250  # the month's first bar (about 100k USD)
D, S = addr("big-deployer"), addr("small-deployer")
EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}


def _coin(mint: str, creator: str | None, **crossed: float) -> dict:
    """A coin's record as `history amm-peaks` writes it (analysis/amm_peaks.py), with the bars it passed and when."""
    rec = {"mint": mint, "creator": creator, "pool": f"{mint}-pool", "first_slot": 1, "first_ts": T0 - 9 * H,
           "last_ts": T0 + 9 * H, "n": 100, "peak_sol": 70_000.0, "peak_slot": 2, "peak_ts": T0}  # fmt: skip
    for bar in ap.BARS_SOL:
        s, t = ap.bar_keys(bar)
        when = crossed.get(t)
        rec[s], rec[t] = (int(when) if when is not None else None), when
    return rec


def _peaks(data: Path, coins: list[dict], name: str = "amm_peaks_2026-10-25_2026-11-23.jsonl") -> Path:
    path = ap.peaks_dir(data) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(c) + "\n" for c in coins))
    return path


def _launch(creator: str, ts: int, user: str | None = None, mint: str = "M") -> Launch:
    """A create: `creator` is the coin's creator (pump.fun's fee and metadata creator), `user` the wallet that signed
    it (the creator itself unless said)."""
    return Launch(mint=addr(mint), user=user or creator, creator=creator, name="T", symbol="T", uri="",
                  bonding_curve=addr(f"{mint}-bc"), slot=SLOT0, ts=ts, sig=addr(f"{mint}-c"))  # fmt: skip


# -- who counts as big, and since when -------------------------------------------------------------------------------


def test_a_creator_counts_as_big_from_the_first_crossing_of_any_of_his_coins(tmp_path):
    path = _peaks(tmp_path, [_coin("A", D, t_1250=T0 + 100), _coin("B", D, t_1250=T0 + 50, t_6250=T0 + 900),
                             _coin("C", S)])  # fmt: skip
    big = fu.big_since(ap.read_peaks([path]), (1_250, 6_250))
    assert big[1_250] == {D: T0 + 50} and big[6_250] == {D: T0 + 900}  # S never passed a bar: never big


def test_a_coin_the_vendor_names_no_creator_for_takes_the_creator_the_tape_recorded(tmp_path):
    path = _peaks(tmp_path, [_coin("A", None, t_1250=T0 + 100), _coin("B", None, t_1250=T0 + 70)])
    big = fu.big_since(ap.read_peaks([path]), (1_250,), creator_of={"A": D})
    assert big[1_250] == {D: T0 + 100}  # B's creator is known to nobody: it makes no one big


def test_the_peaks_of_every_file_count_and_the_earliest_crossing_wins(tmp_path):
    a = _peaks(tmp_path, [_coin("A", D, t_1250=T0 + 500)], "amm_peaks_2026-11-01_2026-11-23.jsonl")
    b = _peaks(tmp_path, [_coin("A", D, t_1250=T0 + 200)], "amm_peaks_2026-10-25_2026-10-31.jsonl")
    assert sorted(ap.peaks_files(tmp_path)) == sorted([a, b])
    assert fu.big_since(ap.read_peaks(ap.peaks_files(tmp_path)), (1_250,))[1_250] == {D: T0 + 200}


# -- which launch a big creator funded ---------------------------------------------------------------------------------


def test_a_launch_is_funded_only_when_the_payment_came_before_the_create_and_the_funder_was_big_well_before_it():
    lead, at = fu.LEAD_SECS, _launch(addr("C"), T0 + 1000)
    paid = {addr("C"): ((T0 + 10, D),)}
    assert lead >= 60  # a watcher needs time to see a crossing and follow the creator's wallets
    assert fu.funded_by(at, (BIG,), paid, {BIG: {D: T0 + 1000 - lead}}) == {BIG: D}
    # the funder's coin went big too late to follow before this create, or after it: hindsight, refused
    assert fu.funded_by(at, (BIG,), paid, {BIG: {D: T0 + 1001 - lead}}) == {BIG: None}
    assert fu.funded_by(at, (BIG,), paid, {BIG: {D: T0 + 1500}}) == {BIG: None}
    assert fu.funded_by(at, (BIG,), {addr("C"): ((T0 + 999, D),)}, {BIG: {D: T0}}) == {BIG: D}
    assert fu.funded_by(at, (BIG,), {addr("C"): ((T0 + 1000, D),)}, {BIG: {D: T0}}) == {BIG: None}  # the create's s
    assert fu.funded_by(at, (BIG,), {addr("C"): ((T0 + 1100, D),)}, {BIG: {D: T0}}) == {BIG: None}  # paid after it
    assert fu.funded_by(None, (BIG,), paid, {BIG: {D: T0}}) == {BIG: None}  # no create on the tape


def test_the_signer_of_the_create_counts_as_well_as_the_creator_it_names():
    at = _launch(addr("someone"), T0 + 2000, user=addr("C"))
    assert fu.funded_by(at, (BIG,), {addr("C"): ((T0 + 10, D),)}, {BIG: {D: T0}}) == {BIG: D}
    # the big creator's own next coin, signed by a wallet he paid, is his launch, not one he funded
    own = _launch(D, T0 + 2000, user=addr("C"))
    assert fu.funded_by(own, (BIG,), {addr("C"): ((T0 + 10, D),)}, {BIG: {D: T0}}) == {BIG: None}


def test_the_big_creator_who_paid_first_is_the_funder():
    d2 = addr("big-deployer-2")
    funders = {addr("C"): ((T0 + 3, d2), (T0 + 8, D))}
    at = _launch(addr("C"), T0 + 2000)
    assert fu.funded_by(at, (BIG,), funders, {BIG: {D: T0, d2: T0 + 1}}) == {BIG: d2}
    # the first payer went big only after paying, still long enough before the create: he is the funder
    assert fu.funded_by(at, (BIG,), funders, {BIG: {D: T0, d2: T0 + 900}}) == {BIG: d2}
    # too late to follow: the next payer who was big long enough before it
    assert fu.funded_by(at, (BIG,), funders, {BIG: {D: T0, d2: T0 + 1990}}) == {BIG: D}


def test_each_bar_is_judged_on_its_own():
    big = {1_250: {D: T0}, 6_250: {D: T0 + 1990}}
    at = _launch(addr("C"), T0 + 2000)
    assert fu.funded_by(at, (1_250, 6_250), {addr("C"): ((T0 + 10, D),)}, big) == {1_250: D, 6_250: None}


def test_the_candidates_are_the_launches_of_paid_wallets_inside_the_tape_by_creator_or_signer():
    store = Store(":memory:")
    paid = addr("C")
    store.add_launches([_launch(paid, T0 + 100, mint="by-creator"), _launch(addr("x"), T0 + 200, user=paid, mint="by-signer"),
                        _launch(addr("y"), T0 + 300, mint="other"), _launch(paid, T0 + 3 * H - 59, mint="too-late")])  # fmt: skip
    cands = fu.candidate_launches(store, [(T0, T0 + 3 * H)], {paid})
    # a launch rule fires no later than a minute before its stretch of tape ends: "too-late" can never fire
    assert [c.mint for c in cands] == [addr("by-creator"), addr("by-signer")]


def test_the_links_kept_are_the_big_creators_payments_only():
    store = Store(":memory:")
    store.upsert_edges([(D, addr("C1"), "paid_by_dev", T0 + 10, 5), (D, addr("C1"), "paid_by_dev", T0 + 4, 5),
                        (S, addr("C2"), "paid_by_dev", T0 + 10, 5), (D, addr("C3"), "funded_by", T0 + 10, 5)])  # fmt: skip
    links = fu.paid_links(store, {D})
    assert links == {addr("C1"): ((T0 + 4, D),)}  # the first payment; S is no big creator; another kind of link


# -- the knob ----------------------------------------------------------------------------------------------------------


def test_funded_is_a_launch_knob_set_to_one_of_the_months_bars():
    trig = parse_trigger({"kind": "launch", "funded": 1250})
    assert trig.funded == 1250.0 and "paid before the create" in trig.label()
    with pytest.raises(ValueError, match="1250, 6250, 12500 or 62500"):
        parse_trigger({"kind": "launch", "funded": 1000})
    with pytest.raises(ValueError, match="does not apply to a hunt trigger"):
        parse_trigger({"kind": "hunt", "sol": 4, "window": 1, "funded": 1250})
    assert parse_trigger({"kind": "launch"}).funded == 0 and "paid" not in Trigger("launch").label()


def test_a_funded_rule_is_research_only_and_no_sniper_is_built_for_it():
    trig = Trigger("launch", funded=1250.0)
    why = research_only_why(trig)
    assert why and "Web mode" in why and research_only_why(Trigger("launch")) is None
    with pytest.raises(ValueError, match="paid before the create"):
        strategy_sniper({"label": "t", "buy_sol": 0.2, "exit": EXIT, "trigger": trig.__dict__}, 1)


def test_a_rule_without_the_knob_keeps_the_id_it_had_before_the_knob_existed():
    trig = dict(Trigger("launch").__dict__)
    before = {k: v for k, v in trig.items() if k != "funded"}
    assert paper.strategy_id(trig, EXIT, "+3", 1, 0.2) == paper.strategy_id(before, EXIT, "+3", 1, 0.2)
    funded = dict(trig, funded=1250.0)
    assert paper.strategy_id(funded, EXIT, "+3", 1, 0.2) != paper.strategy_id(trig, EXIT, "+3", 1, 0.2)


# -- the lab -------------------------------------------------------------------------------------------------------------


def _cfg(data: Path) -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    cfg.storage.db_path = str(data / "snipetech.db")
    return cfg


def _world(store: Store, data: Path) -> dict[str, str]:
    """D's coin passes the first bar at T0 + 100. Six creators launch: C1 (paid by D at +500, launches at +1200) and
    C5 (paid by D at +500, launches at +1600) are the two funded launches; C2 was paid by D at +50 and launched at +80,
    before D's coin went big; C6, paid at +50 too, launched at +250, too soon after it for a watcher to follow D; C3
    launched at +1300 and was paid only at +1400; C4 was paid by S, who never had a big coin. Plus twenty launches
    nobody paid."""
    _peaks(data, [_coin("D-coin", D, t_1250=T0 + 100), _coin("S-coin", S)])
    plan = {"C1": (T0 + 500, D, T0 + 1200), "C2": (T0 + 50, D, T0 + 80), "C3": (T0 + 1400, D, T0 + 1300),
            "C4": (T0 + 500, S, T0 + 1500), "C5": (T0 + 500, D, T0 + 1600), "C6": (T0 + 50, D, T0 + 250)}  # fmt: skip
    mints = {}
    for k, (name, (paid_at, payer, launch_at)) in enumerate(plan.items()):
        store.upsert_edges([(payer, addr(name), "paid_by_dev", paid_at, 1_000_000_000)])
        mints[name] = pump_launch(store, k, addr(name), launch_at, SLOT0 + 10_000 * k).mint
    for k in range(20):
        pump_launch(store, 100 + k, addr(f"nobody-{k}"), T0 + 2000 + 60 * k, SLOT0 + 100_000 + 1000 * k)
    store.set_coverage("tape", {"spans": [[T0, T0 + 3 * H]]})
    return mints


def test_the_lab_scores_only_the_launches_a_big_creator_funded_before_the_create(tmp_path):
    store = Store(":memory:")
    mints = _world(store, tmp_path)
    data = collect(store, _cfg(tmp_path), 0.2, 0, "start", triggers=[Trigger("launch", funded=1250.0)],
                   landings=paper.LAUNCH_LANDINGS)  # fmt: skip
    rows = data["rows"]
    assert {r["mint"] for r in rows} == {mints["C1"], mints["C5"]}
    assert {r["funder"] for r in rows} == {D}
    # one funder's launches are one draw of the bounds: a team that funds wallet after wallet is one team
    assert len(set(coin_keys(rows))) == 1
    web = data["funded"]
    assert web["big_creators"] == {"1250": 1} and web["funded_wallets"] == 5  # C1, C2, C3, C5, C6: paid by D
    assert web["launches"] == 5 and web["funded_launches"] == {"1250": 2}
    assert web["peaks_files"] == ["amm_peaks_2026-10-25_2026-11-23.jsonl"] and web["lead_secs"] == fu.LEAD_SECS


def test_a_lab_with_other_rules_too_reads_every_launch_and_fires_the_funded_rule_on_the_same_two(tmp_path):
    store = Store(":memory:")
    mints = _world(store, tmp_path)
    data = collect(store, _cfg(tmp_path), 0.2, 0, "start",
                   triggers=[Trigger("launch", funded=1250.0), Trigger("launch")], landings=paper.LAUNCH_LANDINGS)  # fmt: skip
    funded = {r["mint"] for r in data["rows"] if r["trigger"] == 0}
    every = {r["mint"] for r in data["rows"] if r["trigger"] == 1}
    assert funded == {mints["C1"], mints["C5"]} and len(every) == 26
    assert not any("funder" in r for r in data["rows"] if r["trigger"] == 1)  # the plain rule's draws stay coins


def test_a_funded_rule_without_the_months_crossings_is_refused_in_words(tmp_path):
    store = Store(":memory:")
    store.set_coverage("tape", {"spans": [[T0, T0 + 3 * H]]})
    with pytest.raises(ValueError, match="history amm-peaks"):
        collect(store, _cfg(tmp_path), 0.2, 0, "start", triggers=[Trigger("launch", funded=1250.0)],
                landings=paper.LAUNCH_LANDINGS)  # fmt: skip

"""Which accounts are tip accounts, learned from the amounts sent to them (`solana/pay_dests.py`, 14 Nov).

A platform takes a share of the trade: what it gets follows the trade's size. A landing service is tipped for the
place in the block: what it gets does not. The module keeps, for each destination a trade's transaction paid, a
sample of (lamports paid, the trade's size) and says "tip" when the two do not move together, "fee" when they do, and
nothing while it cannot tell. Synthetic samples in the shapes the real transactions show (tests/test_tx_pays.py);
every random number comes from a generator the test seeds.
"""

import json
import random

import pytest

from snipetech.solana import pay_dests as P
from snipetech.solana.pay_dests import PayDests, classify, spearman

SOL = 10**9


def trades(rng: random.Random, n: int) -> list[int]:
    """Trade sizes as the tape has them: from 0.02 to 20 SOL, most of them small."""
    return [int(SOL * 0.02 * 1000 ** rng.random()) for _ in range(n)]


def test_a_flat_amount_over_trades_of_every_size_is_a_tip():
    rng = random.Random(1)
    samples = [(1_750_000, q) for q in trades(rng, 120)]  # the Decu shape: 1,750,000 lamports whatever the trade
    kind, info = classify(samples, n=120, payers=14)
    assert kind == "tip"
    assert info["rho"] == 0.0  # a constant amount moves with nothing
    assert info["median_lamports"] == 1_750_000 and info["median_pct"] < 1.0


def test_a_fee_and_a_transfer_that_add_up_to_a_round_number_are_a_tip():
    """The Axiom shape: the bot bids 800,000 lamports in all; what the priority fee does not take goes to the tip
    account (796,456 with a fee of 3,544; 783,612 with 16,388). The amount wobbles, but not with the trade."""
    rng = random.Random(2)
    samples = [(800_000 - rng.choice((1_735, 3_544, 15_773, 16_388, 17_827, 51_802)), q) for q in trades(rng, 200)]
    kind, info = classify(samples, n=200, payers=31)
    assert kind == "tip" and abs(info["rho"]) < 0.2
    assert 745_000 < info["median_lamports"] < 800_000


def test_several_bots_each_with_its_own_flat_tip_are_a_tip():
    rng = random.Random(3)
    samples = [(rng.choice((10_000, 200_000, 1_000_000, 5_000_000)), q) for q in trades(rng, 256)]
    assert classify(samples, n=4_000, payers=64)[0] == "tip"


def test_one_percent_of_the_trade_in_two_tiers_is_a_fee():
    rng = random.Random(4)
    samples = [(int(q * rng.choice((0.00987, 0.01023))), q) for q in trades(rng, 200)]  # Axiom's fee accounts
    kind, info = classify(samples, n=200, payers=40)
    assert kind == "fee" and info["rho"] > 0.99
    assert 0.98 < info["median_pct"] < 1.03


def test_a_referral_share_in_two_tiers_is_a_fee():
    rng = random.Random(5)
    samples = [(int(q * rng.choice((0.00254, 0.00458))), q) for q in trades(rng, 200)]
    kind, info = classify(samples, n=200, payers=40)
    assert kind == "fee" and info["rho"] > 0.9
    assert 0.25 <= info["median_pct"] <= 0.46


def test_nine_payers_or_twenty_nine_samples_decide_nothing():
    rng = random.Random(6)
    flat = [(1_000_000, q) for q in trades(rng, 120)]
    assert classify(flat, n=120, payers=9) == (None, classify(flat, n=120, payers=10)[1])
    assert classify(flat, n=120, payers=10)[0] == "tip"
    assert classify(flat[:29], n=29, payers=20)[0] is None
    assert classify(flat[:30], n=30, payers=20)[0] == "tip"
    assert (P.MIN_N, P.MIN_PAYERS) == (30, 10)


def test_between_the_two_bars_nothing_is_decided():
    """Bigger traders tipping more: the amount follows the size loosely (here 0.5 to 0.7). Not a tip by this rule,
    not a fee: undecided, and said so, never guessed."""
    rng = random.Random(7)
    samples = []
    for q in trades(rng, 256):
        base = 100_000 if q < SOL else 2_000_000
        samples.append((int(base * rng.choice((0.1, 1, 1, 10))), q))
    kind, info = classify(samples, n=256, payers=50)
    assert P.TIP_MAX_RHO <= info["rho"] < P.FEE_MIN_RHO and kind is None


def test_a_learned_tip_account_stays_one_through_an_odd_hour():
    """Hysteresis: learned as a tip (rho under 0.3), it stays one while rho is under 0.5; an account never learned
    needs the lower bar."""
    rng = random.Random(8)
    quotes = trades(rng, 256)

    def mixed(share: float) -> list[tuple[int, int]]:  # `share` of the samples follow the trade, the rest are flat
        return [((q // 100) if rng.random() < share else 1_000_000, q) for q in quotes]

    low = mixed(0.45)
    rho = classify(low, n=256, payers=30)[1]["rho"]
    assert P.TIP_MAX_RHO <= rho < P.TIP_KEEP_RHO, rho
    assert classify(low, n=256, payers=30)[0] is None
    assert classify(low, n=256, payers=30, previous="tip")[0] == "tip"
    assert classify(low, n=256, payers=30, previous="fee")[0] is None
    high = mixed(0.75)
    assert classify(high, n=256, payers=30)[1]["rho"] >= P.TIP_KEEP_RHO
    assert classify(high, n=256, payers=30, previous="tip")[0] != "tip"
    assert (P.TIP_MAX_RHO, P.TIP_KEEP_RHO, P.FEE_MIN_RHO) == (0.3, 0.5, 0.8)


def test_the_same_amount_on_trades_of_one_size_says_nothing():
    """A bot that always buys 0.5 SOL through a platform pays it the same 5,000,000 lamports every time: a constant
    amount, and a fee. Without trades of different sizes the amount cannot be told from a tip, so nothing is decided
    until the trades it came with differ (the 90th percentile at least three times the 10th)."""
    same = [(5_000_000, SOL // 2)] * 200
    kind, info = classify(same, n=200, payers=25)
    assert kind is None and info["rho"] == 0.0 and info["quote_spread"] == 1.0
    assert info["median_pct"] == 1.0
    assert P.MIN_QUOTE_SPREAD == 3.0
    narrow = [(5_000_000, SOL // 2 + k * 1_000_000) for k in range(200)]  # 0.5 to 0.7 SOL
    assert classify(narrow, n=200, payers=25)[0] is None
    assert classify(narrow, n=200, payers=25, previous="tip")[0] == "tip"  # learned on better evidence: kept


def test_an_amount_as_large_as_the_trade_is_the_trades_own_money_not_a_tip():
    """A router's holding account, or a wallet swept after its sale, gets SOL that does not follow the trade's size
    closely and is as large as the trade itself. A tip is a bid beside the trade: a destination whose median payment
    is half the trade or more is never read as a tip account."""
    rng = random.Random(14)
    quotes = trades(rng, 200)
    sweep = [(int(SOL * rng.uniform(0.5, 40)), q) for q in quotes]  # whatever the wallet held: unrelated to the trade
    kind, info = classify(sweep, n=200, payers=40)
    assert abs(info["rho"]) < P.TIP_MAX_RHO and info["median_pct"] >= P.MAX_TIP_MEDIAN_PCT == 50.0
    assert kind is None
    assert classify(sweep, n=200, payers=40, previous="tip")[0] is None  # not kept as one either
    small = [(lamports // 10_000, q) for lamports, q in sweep]  # the same shape at a ten-thousandth: a tip
    assert classify(small, n=200, payers=40)[0] == "tip"


def test_the_rank_correlation_handles_ties():
    assert spearman([1, 2, 3, 4, 5], [5, 6, 7, 8, 7]) == pytest.approx(0.8207826816681233)  # scipy: 0.82078...
    assert spearman([1, 2, 2, 3], [1, 3, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([7, 7, 7, 7], [1, 2, 3, 4]) == 0.0 and spearman([1, 2, 3], [5, 5, 5]) == 0.0
    assert spearman([], []) == 0.0 and spearman([1], [1]) == 0.0
    # ties get the mean of the ranks they span: 10, 20, 20, 30 -> 1, 2.5, 2.5, 4
    assert spearman([10, 20, 20, 30], [1, 2, 3, 4]) == pytest.approx(0.9486832980505138)


def test_each_destination_keeps_its_count_its_payers_and_a_bounded_sample():
    pd = PayDests()
    for k in range(1_000):
        pd.add("TIP", 1_000_000 + k, (k + 1) * 10_000_000, f"payer{k % 100}", 1_000 + k)
    d = pd.get("TIP")
    assert d["n"] == 1_000 and d["first_ts"] == 1_000 and d["last_ts"] == 1_999
    assert d["payers"] == P.PAYERS_CAP == 64  # a hundred wallets paid it: counted up to the cap
    assert len(d["samples"]) == P.SAMPLES_CAP == 256
    seen = sorted(lamports - 1_000_000 for lamports, _quote in d["samples"])
    assert seen[0] < 100 and seen[-1] > 900  # the sample spans the whole history, not its first or last 256
    assert sum(1 for k in seen if k >= 500) > 64  # and its later half is in it
    assert all(quote == (lamports - 1_000_000 + 1) * 10_000_000 for lamports, quote in d["samples"])  # pairs stay pairs
    pd.add("ONCE", 5, 7, "payer0", 3_000)
    assert pd.get("ONCE") == {
        "address": "ONCE", "kind": None, "n": 1, "payers": 1, "rho": None, "median_lamports": None,
        "median_pct": None, "first_ts": 3_000, "last_ts": 3_000, "tip_since": None, "samples": [(5, 7)],
    }  # fmt: skip
    assert len(pd) == 2 and pd.get("NOBODY") is None


def test_the_sample_is_deterministic():
    """No randomness of its own: the same payments give the same sample, in this process or the next."""
    a, b = PayDests(), PayDests()
    rng = random.Random(9)
    feed = [(rng.randrange(1, 10**7), rng.randrange(1, 10**10), f"p{rng.randrange(40)}", t) for t in range(3_000)]
    for lamports, quote, payer, ts in feed:
        a.add("D", lamports, quote, payer, ts)
        b.add("D", lamports, quote, payer, ts)
    assert a.get("D") == b.get("D")
    assert a.get("D")["samples"] != [(lamports, quote) for lamports, quote, _p, _t in feed[:256]]


def test_a_payment_that_cannot_be_counted_is_left_out():
    pd = PayDests()
    pd.add("D", 0, 10**9, "p", 1)  # nothing paid
    pd.add("D", -5, 10**9, "p", 1)
    pd.add("D", 1_000, 0, "p", 1)  # no trade to compare it with
    pd.add("", 1_000, 10**9, "p", 1)
    assert len(pd) == 0 and pd.dirty == set()


def test_when_full_the_one_payer_destinations_seen_longest_ago_go_first():
    pd = PayDests(max_dests=20)
    for k in range(3):  # three destinations several wallets paid, seen first of all
        for payer in ("a", "b", "c"):
            pd.add(f"SHARED{k}", 1_000, 10**9, payer, 10 + k)
    for k in range(17):
        pd.add(f"ONCE{k}", 1_000, 10**9, "a", 100 + k)
    assert len(pd) == 20
    pd.add("NEW", 1_000, 10**9, "a", 500)  # the twenty-first
    assert len(pd) <= 20 and pd.get("NEW") is not None
    assert pd.get("ONCE0") is None  # the one-payer destination seen longest ago
    assert all(pd.get(f"SHARED{k}") is not None for k in range(3))  # older still, but several wallets paid them
    assert pd.get("ONCE16") is not None
    assert pd.dropped >= 1
    for k in range(200):  # a flood of one-payer destinations never pushes the shared ones out
        pd.add(f"FLOOD{k}", 1_000, 10**9, "z", 1_000 + k)
    assert len(pd) <= 20 and all(pd.get(f"SHARED{k}") is not None for k in range(3))
    full = PayDests(max_dests=4)
    for k in range(6):  # only shared destinations: the one seen longest ago goes
        for payer in ("a", "b"):
            full.add(f"S{k}", 1, 10**9, payer, k)
    assert len(full) <= 4 and full.get("S0") is None and full.get("S5") is not None
    assert P.MAX_DESTS == 50_000


def _learn(pd: PayDests, rng: random.Random, start: int = 0) -> None:
    for k, q in enumerate(trades(rng, 400)):
        payer = f"wallet{k % 25}"
        pd.add("TIPACC", 1_000_000, q, payer, start + k)
        pd.add("FEEACC", q // 100, q, payer, start + k)
        if k < 20:
            pd.add("YOUNG", 1_000_000, q, payer, start + k)


def test_refresh_classifies_what_changed_and_remembers_since_when():
    pd = PayDests()
    _learn(pd, random.Random(10))
    assert pd.dirty == {"TIPACC", "FEEACC", "YOUNG"}
    assert pd.refresh(pd.dirty, now=5_000) == 3
    tip, fee, young = pd.get("TIPACC"), pd.get("FEEACC"), pd.get("YOUNG")
    assert (tip["kind"], tip["tip_since"], tip["rho"], tip["median_lamports"]) == ("tip", 5_000, 0.0, 1_000_000)
    assert (fee["kind"], fee["tip_since"]) == ("fee", None) and fee["rho"] > 0.99 and fee["median_pct"] == 1.0
    assert (young["kind"], young["n"], young["payers"]) == (None, 20, 20)
    assert pd.tips() == {"TIPACC": 5_000}
    pd.refresh({"TIPACC"}, now=9_000)
    assert pd.get("TIPACC")["tip_since"] == 5_000  # kept from the first time
    # the account starts taking a share of the trade: once it no longer reads as a tip the mark is cleared
    rng = random.Random(11)
    for k, q in enumerate(trades(rng, 60_000)):
        pd.add("TIPACC", q // 100, q, f"wallet{k % 25}", 10_000 + k)
    pd.refresh({"TIPACC"}, now=80_000)
    assert pd.get("TIPACC")["kind"] == "fee" and pd.get("TIPACC")["tip_since"] is None and pd.tips() == {}


def test_rows_go_to_the_store_and_load_brings_the_learning_back():
    pd = PayDests()
    _learn(pd, random.Random(12))
    pd.refresh(pd.dirty, now=5_000)
    rows = pd.rows()
    assert {r["address"] for r in rows} == {"TIPACC", "FEEACC", "YOUNG"}
    assert [r["address"] for r in pd.rows({"FEEACC", "GONE"})] == ["FEEACC"]
    tip = next(r for r in rows if r["address"] == "TIPACC")
    assert set(tip) == {"address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts",
                        "last_ts", "tip_since", "samples"}  # fmt: skip
    assert isinstance(tip["samples"], str) and len(json.loads(tip["samples"])) == 2 * 256  # flat: lamports, quote, ...
    back = PayDests()
    assert back.load(rows) == 3
    assert back.dirty == set()  # what was loaded is what the table holds
    for address in ("TIPACC", "FEEACC", "YOUNG"):
        assert back.get(address) == pd.get(address)
    assert back.tips() == {"TIPACC": 5_000}
    # and it goes on from there: the same payments after the restart give the same state as without one
    _learn(pd, random.Random(13), start=1_000)
    _learn(back, random.Random(13), start=1_000)
    pd.refresh(pd.dirty, now=7_000)
    back.refresh(back.dirty, now=7_000)
    assert back.get("YOUNG")["payers"] == 20  # the wallets are not kept: the count never falls below the stored one
    for address in ("TIPACC", "FEEACC"):
        assert back.get(address) == pd.get(address)
    assert back.get("YOUNG")["n"] == 40 and back.get("TIPACC")["tip_since"] == 5_000


def test_a_bad_row_is_left_out_of_a_load_not_the_whole_table():
    pd = PayDests()
    good = {"address": "A", "kind": "tip", "n": 40, "payers": 12, "rho": 0.0, "median_lamports": 5, "median_pct": 0.1,
            "first_ts": 1, "last_ts": 2, "tip_since": 2, "samples": "[5, 100, 5, 200]"}  # fmt: skip
    rows = [good, {**good, "address": "B", "samples": "not json"}, {**good, "address": "C", "n": None},
            {"address": "D"}, {**good, "address": "E", "samples": None}, {**good, "address": ""}]  # fmt: skip
    assert pd.load(rows) == 2
    assert pd.get("A")["samples"] == [(5, 100), (5, 200)] and pd.get("E")["samples"] == [] and pd.get("B") is None
    assert pd.tips() == {"A": 2, "E": 2}


def test_the_module_is_pure_and_light():
    """No clock, no file, no network, and nothing heavy to load: the tape recorder imports it."""
    import subprocess
    import sys

    code = ("import sys\nimport snipetech.solana.pay_dests as P\n"
            "print(sorted(m for m in ('numpy', 'time', 'random', 'sqlite3', 'httpx') if m in vars(P)))\n"
            "print('numpy' in sys.modules)")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split("\n")
    assert out[:2] == ["[]", "False"]

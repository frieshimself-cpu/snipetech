"""Which accounts are tip accounts, learned from the amounts sent to them (`solana/pay_dests.py`, 14 Nov).

A platform takes a share of the trade: what it gets follows the trade's size. A landing service is tipped for the
place in the block: what it gets does not. The module keeps, for each destination a trade's transaction paid, a
sample of (lamports paid, the trade's size, the paying wallet) and says "tip" when the two do not move together, "fee"
when they do, "mixed" when a part of the payments is one common share of the trade, and nothing while it cannot tell.

Round 2 (two reviews of the first version): no wallet holds more than a tenth of a sample, so one wallet cannot
decide a destination either way; a mixed account is never "tip"; the first "tip" needs two readings in a row; and
being applied to a trade's tip needs 25 different wallets in the sample and "tip" at two flushes in a row. Nothing a
payment can do loses what was learned: not a full table, not a restart whose read failed.

Synthetic samples in the shapes the real transactions show (tests/test_tx_pays.py) and the populations the reviewers
simulated; every random number comes from a generator the test seeds.
"""

import json
import math
import random

import pytest

from snipetech.solana import pay_dests as P
from snipetech.solana.pay_dests import PayDests, classify, common_ratio_share, spearman

SOL = 10**9


def trades(rng: random.Random, n: int) -> list[int]:
    """Trade sizes as the tape has them: from 0.02 to 20 SOL, most of them small."""
    return [int(SOL * 0.02 * 1000 ** rng.random()) for _ in range(n)]


def wallet(k: int) -> str:
    return f"W{k:04d}wallet"  # its first eight characters are its own


# ---- one reading -----------------------------------------------------------------------------------------------------


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


def test_nine_wallets_or_twenty_nine_pairs_decide_nothing():
    flat = [(1_000_000, int(SOL * 0.02 * 1000 ** (k / 119))) for k in range(0, 120, 4)]  # thirty sizes, evenly apart
    flat += [(1_000_000, int(SOL * 0.02 * 1000 ** (k / 119))) for k in range(120) if k % 4]
    assert classify(flat, n=120, payers=9) == (None, classify(flat, n=120, payers=10)[1])
    assert classify(flat, n=120, payers=10)[0] == "tip"
    assert classify(flat[:29], n=29, payers=20)[0] is None
    assert classify(flat[:30], n=30, payers=20)[0] == "tip"
    # the payments counted are the sample's: thirty thousand payments of which the sample holds 29 pairs decide nothing
    assert classify(flat[:29], n=30_000, payers=20)[0] is None
    assert (P.MIN_N, P.MIN_PAYERS) == (30, 10)


def loosely(rng: random.Random, quotes: list[int], power: float) -> list[tuple[int, int]]:
    """Amounts that follow the trade's size loosely (bigger traders tipping more): no common share of the trade."""
    return [(int(50_000 * (q / 1e7) ** power * math.exp(rng.gauss(0, 1.0))) + 1, q) for q in quotes]


def test_between_the_two_bars_nothing_is_decided():
    """Bigger traders tipping more: the amount follows the size loosely. Not a tip by this rule, not a fee:
    undecided, and said so, never guessed."""
    rng = random.Random(7)
    samples = loosely(rng, trades(rng, 256), 0.45)
    kind, info = classify(samples, n=256, payers=50)
    assert P.TIP_KEEP_RHO <= info["rho"] < P.FEE_KEEP_RHO and kind is None
    assert classify(samples, n=256, payers=50, previous="tip")[0] is None
    assert classify(samples, n=256, payers=50, previous="fee")[0] is None


def test_a_learned_tip_account_stays_one_through_an_odd_hour():
    """Hysteresis: read as a tip (rho under 0.3), it stays one while rho is under 0.5; an account never read as one
    needs the lower bar."""
    rng = random.Random(8)
    quotes = trades(rng, 256)
    low = loosely(rng, quotes, 0.2)
    rho = classify(low, n=256, payers=30)[1]["rho"]
    assert P.TIP_MAX_RHO <= rho < P.TIP_KEEP_RHO, rho
    assert classify(low, n=256, payers=30)[0] is None
    assert classify(low, n=256, payers=30, previous="tip")[0] == "tip"
    assert classify(low, n=256, payers=30, previous="fee")[0] is None
    assert (P.TIP_MAX_RHO, P.TIP_KEEP_RHO, P.FEE_MIN_RHO) == (0.3, 0.5, 0.8)


def test_a_fee_account_stays_one_down_to_point_seven():
    """D9: the same idea for "fee": read as one at 0.8 or more, it stays one down to 0.7."""
    rng = random.Random(9)
    quotes = trades(rng, 256)
    mid = loosely(rng, quotes, 0.55)
    rho = classify(mid, n=256, payers=30)[1]["rho"]
    assert P.FEE_KEEP_RHO <= rho < P.FEE_MIN_RHO, rho
    assert classify(mid, n=256, payers=30)[0] is None
    assert classify(mid, n=256, payers=30, previous="fee")[0] == "fee"
    assert classify(mid, n=256, payers=30, previous="tip")[0] is None
    assert P.FEE_KEEP_RHO == 0.7


def test_the_same_amount_on_trades_of_one_size_is_never_a_tip():
    """A bot that always buys 0.5 SOL through a platform pays it the same 5,000,000 lamports every time: a constant
    amount, and a fee. On trades of one size the amount cannot be told from a flat tip: it is one common share of
    the trade, so the account is never read as a tip account."""
    same = [(5_000_000, SOL // 2)] * 200
    kind, info = classify(same, n=200, payers=25)
    assert kind == "mixed" and info["rho"] == 0.0 and info["quote_spread"] == 1.0
    assert info["median_pct"] == 1.0 and info["common_ratio_share"] == 1.0
    assert classify(same, n=200, payers=25, previous="tip")[0] == "mixed"
    assert P.MIN_QUOTE_SPREAD == 3.0
    # trades of nearly one size and a flat amount, no common share among them: undecided until the sizes differ
    rng = random.Random(10)
    narrow = [(rng.choice((200_000, 900_000, 4_000_000)), SOL // 2 + k * 2_000_000) for k in range(200)]
    kind, info = classify(narrow, n=200, payers=25)
    assert info["quote_spread"] < 3.0 and info["common_ratio_share"] < P.MIXED_SHARE and kind is None
    assert classify(narrow, n=200, payers=25, previous="tip")[0] == "tip"  # read on better evidence before: kept


def test_an_amount_as_large_as_the_trade_is_the_trades_own_money_not_a_tip():
    """A router's holding account, or a wallet swept after its sale, gets SOL that does not follow the trade's size
    closely and is as large as the trade itself. A tip is a bid beside the trade: a destination whose median payment
    is half the trade or more is never newly read as a tip account. D8: one already read as a tip stays one up to a
    median of three quarters of the trade, so an account at the bar does not flip with every flush."""
    rng = random.Random(14)
    quotes = trades(rng, 200)

    def at(share: float) -> list[tuple[int, int]]:  # amounts unrelated to the trade, their median `share` of it
        return [(int(sorted(quotes)[100] * share * math.exp(rng.gauss(0, 0.6))), q) for q in quotes]

    def median_pct(samples) -> float:
        return classify(samples, n=200, payers=40)[1]["median_pct"]

    big = at(5.0)
    assert median_pct(big) >= P.KEEP_TIP_MEDIAN_PCT == 75.0 and abs(classify(big, 200, 40)[1]["rho"]) < P.TIP_MAX_RHO
    assert classify(big, n=200, payers=40)[0] is None
    assert classify(big, n=200, payers=40, previous="tip")[0] is None  # not kept either
    between = next(s for s in (at(x) for x in (0.5, 0.55, 0.6, 0.65, 0.7)) if 50.0 <= median_pct(s) < 75.0)
    assert classify(between, n=200, payers=40)[0] is None  # not newly a tip
    assert classify(between, n=200, payers=40, previous="tip")[0] == "tip"  # but kept
    small = [(lamports // 1_000, q) for lamports, q in big]  # the same shape at a thousandth: a tip
    assert classify(small, n=200, payers=40)[0] == "tip"
    assert P.MAX_TIP_MEDIAN_PCT == 50.0


def test_the_rank_correlation_handles_ties():
    assert spearman([1, 2, 3, 4, 5], [5, 6, 7, 8, 7]) == pytest.approx(0.8207826816681233)  # scipy: 0.82078...
    assert spearman([1, 2, 2, 3], [1, 3, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([7, 7, 7, 7], [1, 2, 3, 4]) == 0.0 and spearman([1, 2, 3], [5, 5, 5]) == 0.0
    assert spearman([], []) == 0.0 and spearman([1], [1]) == 0.0
    # ties get the mean of the ranks they span: 10, 20, 20, 30 -> 1, 2.5, 2.5, 4
    assert spearman([10, 20, 20, 30], [1, 2, 3, 4]) == pytest.approx(0.9486832980505138)


# ---- D: a mixed account is not a tip account ------------------------------------------------------------------------


def test_a_tenth_of_the_payments_at_one_share_of_the_trade_make_an_account_mixed_never_tip():
    """An account that gets a platform's one percent on some trades and flat tips on the others. Its rank correlation
    is low (the flat tips are nine in ten), so the first version read it as a tip account, and with the switch on
    each one percent would have been written as a tip: 0.03 SOL on a 3 SOL buy."""
    rng = random.Random(21)
    quotes = trades(rng, 250)
    samples = [((q // 100) if k % 10 == 0 else rng.choice((800_000, 1_000_000, 1_750_000)), q, wallet(k % 60))
               for k, q in enumerate(quotes)]  # fmt: skip
    kind, info = classify(samples, n=250, payers=60)
    assert info["rho"] < P.TIP_MAX_RHO and info["common_ratio_share"] >= P.MIXED_SHARE == 0.10
    assert kind == "mixed"
    assert classify(samples, n=250, payers=60, previous="tip")[0] == "mixed"  # never "tip", whatever it was before
    # the common ratio is found by sorting the ratios: twenty-five pairs within two percent of 0.01
    assert 0.10 <= common_ratio_share([(s[0], s[1]) for s in samples]) <= 0.13  # and a few flat ones beside them
    assert P.RATIO_BAND == 0.02
    # once read as mixed it stays so down to half the bar: an account at the bar does not flip
    fewer = [s for k, s in enumerate(samples) if k % 10 or k % 20 == 0]  # one fee payment in two taken out: 5.5 %
    share = classify(fewer, n=250, payers=60)[1]["common_ratio_share"]
    assert P.MIXED_KEEP <= share < P.MIXED_SHARE
    assert classify(fewer, n=250, payers=60)[0] == "tip"
    assert classify(fewer, n=250, payers=60, previous="mixed")[0] == "mixed"


def test_one_wallet_that_always_pays_one_share_of_its_trades_does_not_make_an_account_mixed():
    """C applied to D: a wallet holds at most a tenth of a sample, and in the count of a common ratio it stands for
    at most MIXED_PER_WALLET pairs. So one bot that pays one percent of its own trades (or always buys one size with
    one tip: one ratio) cannot take a tip account's kind away."""
    rng = random.Random(22)
    quotes = trades(rng, 250)
    samples = [(rng.choice((800_000, 1_000_000, 1_750_000)), q, wallet(k % 60)) for k, q in enumerate(quotes[:225])]
    samples += [(q // 100, q, "the bot") for q in quotes[225:]]  # twenty-five pairs: a tenth, all one wallet's
    kind, info = classify(samples, n=250, payers=61)
    assert info["common_ratio_share"] < P.MIXED_SHARE / 2 and kind == "tip"
    assert common_ratio_share([(s[0], s[1]) for s in samples]) >= 0.10  # without the wallets it would count as mixed
    several = samples[:225] + [(q // 100, q, wallet(900 + k % 6)) for k, q in enumerate(quotes[225:])]
    assert classify(several, n=250, payers=66)[0] == "mixed"  # six wallets paying that share are a stream of fees
    # a young sample: three pairs that fall together by chance, or one wallet's five, are no stream either
    young = [(1_000_000, q, wallet(k)) for k, q in enumerate(quotes[:30])]
    assert common_ratio_share([(1_000, 100_000)] * 3 + [(s[0], s[1]) for s in young]) == 0.0
    assert 6 / 36 <= common_ratio_share([(1_000, 100_000)] * 6 + [(s[0], s[1]) for s in young]) <= 8 / 36
    one = [(1_000, 100_000, "the bot")] * 6 + young
    assert common_ratio_share([(s[0], s[1]) for s in one], [s[2] for s in one]) == 0.0
    assert (P.MIXED_PER_WALLET, P.MIXED_MIN_WALLETS, P.MIXED_MIN_PAIRS) == (5, 3, 5)


class Pop:
    """The reviewers' population (s2_sim.py): wallets with a typical trade size (lognormal, median 0.3 SOL), a Zipf
    activity, and their own trade-to-trade noise."""

    def __init__(self, rng, wallets=300, sigma_w=1.2, sigma_t=0.5, median=0.3, zipf=1.0):
        self.rng = rng
        self.size = [median * math.exp(rng.gauss(0, sigma_w)) for _ in range(wallets)]
        self.weight = [1 / (k + 1) ** zipf for k in range(wallets)]
        rng.shuffle(self.weight)
        self.sigma_t = sigma_t
        self.ids = list(range(wallets))

    def trade(self):
        w = self.rng.choices(self.ids, self.weight)[0]
        return w, max(1000, int(self.size[w] * math.exp(self.rng.gauss(0, self.sigma_t)) * SOL))


ROUND = (200_000, 500_000, 750_000, 800_000, 2_000_000, 5_000_000)


def tip_flat(rng):
    pop = Pop(rng)
    tier = {w: rng.choice(ROUND) for w in pop.ids}
    while True:
        w, q = pop.trade()
        yield tier[w], q, w


def tip_axiom(rng):
    pop = Pop(rng)
    tier = {w: rng.choice(ROUND) for w in pop.ids}
    while True:
        w, q = pop.trade()
        yield tier[w] - min(tier[w] - 1000, int(5000 * math.exp(rng.gauss(0, 1.0)))), q, w


def both(fee_share):
    def gen(rng):
        pop = Pop(rng)
        tier = {w: rng.choice(ROUND) for w in pop.ids}
        while True:
            w, q = pop.trade()
            yield (int(q * 0.01) if rng.random() < fee_share else tier[w]), q, w

    return gen


def final_kinds(gen, seeds: int = 100, n: int = 3_000, flush: int = 50) -> list[tuple[str | None, bool]]:
    """Each seed's population fed as the recorder feeds (a reading every `flush` payments): what it ends as."""
    out = []
    for seed in range(seeds):
        g = gen(random.Random(seed))
        pd = PayDests()
        for i in range(1, n + 1):
            lamports, quote, w = next(g)
            pd.add("D", lamports, quote, wallet(w), i)
            if i % flush == 0:
                pd.refresh(["D"], i)
        d = pd.get("D")
        out.append((d["kind"], d["applied"]))
    return out


@pytest.mark.parametrize("share", [0.10, 0.25, 0.50])
def test_an_account_that_gets_fees_and_tips_ends_not_tip(share):
    """The reviewers' "BOTH" populations: 10, 25 and 50 % of the payments are one percent of the trade, the rest
    flat tips. The first version ended "tip" in 95, 89 and 24 of 100 runs."""
    ends = final_kinds(both(share))
    assert sum(1 for kind, _applied in ends if kind != "tip") >= 95
    assert not any(applied for _kind, applied in ends)


@pytest.mark.parametrize("gen", [tip_flat, tip_axiom], ids=["flat round tiers", "round minus the priority fee"])
def test_a_clean_tip_account_still_ends_tip_and_applied(gen):
    ends = final_kinds(gen)
    assert sum(1 for kind, _applied in ends if kind == "tip") >= 90
    assert sum(1 for _kind, applied in ends if applied) >= 90  # three hundred wallets: the higher bar is met too


# ---- each destination's count, its wallets and its sample -----------------------------------------------------------


def test_each_destination_keeps_its_count_its_payers_and_a_bounded_sample():
    pd = PayDests()
    for k in range(1_000):
        pd.add("TIP", 1_000_000 + k, (k + 1) * 10_000_000, wallet(k % 100), 1_000 + k)
    d = pd.get("TIP")
    assert d["n"] == 1_000 and d["first_ts"] == 1_000 and d["last_ts"] == 1_999
    assert d["payers"] == P.PAYERS_CAP == 64  # a hundred wallets paid it: counted up to the cap
    assert 85 <= d["sample_payers"] <= 100  # the sample's own wallets, all counted (PAYERS_CAP is the life's count)
    assert len(d["samples"]) == P.SAMPLES_CAP == 256
    seen = sorted(lamports - 1_000_000 for lamports, _quote in d["samples"])
    assert seen[0] < 100 and seen[-1] > 900  # the sample spans the whole history, not its first or last 256
    assert sum(1 for k in seen if k >= 500) > 64  # and its later half is in it
    assert all(quote == (lamports - 1_000_000 + 1) * 10_000_000 for lamports, quote in d["samples"])  # pairs stay pairs
    assert sum(pd.sample_wallets("TIP").values()) == 256 and max(pd.sample_wallets("TIP").values()) <= 10
    pd.add("ONCE", 5, 7, wallet(0), 3_000)
    assert pd.get("ONCE") == {
        "address": "ONCE", "kind": None, "n": 1, "payers": 1, "rho": None, "median_lamports": None,
        "median_pct": None, "first_ts": 3_000, "last_ts": 3_000, "tip_since": None, "samples": [(5, 7)],
        "applied": False, "tip_streak": 0, "sample_payers": 1,
    }  # fmt: skip
    assert len(pd) == 2 and pd.get("NOBODY") is None


def test_a_wallet_is_kept_by_the_first_characters_of_its_address():
    """The tag is the same in every process (the wallets of a stored sample still count after a restart) and costs
    no decoding; two wallets sharing it count as one, which can only make the bars harder to reach."""
    assert P.TAG_CHARS == 8 and P.tag_of("AtifzK52uGFf9F4pKjtPcm9QFxzQoESWoqVC1yuihAYD") == "AtifzK52"
    pd = PayDests()
    pd.add("D", 1, 10, "AtifzK52uGFf9F4pKjtPcm9QFxzQoESWoqVC1yuihAYD", 1)
    pd.add("D", 1, 10, "AtifzK52AnotherWalletWithTheSameFirstEight11", 2)
    pd.add("D", 1, 10, "FLPjySnxc4LK5Gksd7p6NzDv4J3w1PbCeugGhYrA4iTU", 3)
    assert pd.sample_wallets("D") == {"AtifzK52": 2, "FLPjySnx": 1} and pd.get("D")["payers"] == 2


def test_the_sample_is_deterministic():
    """No randomness of its own: the same payments give the same sample, in this process or the next."""
    a, b = PayDests(), PayDests()
    rng = random.Random(9)
    feed = [(rng.randrange(1, 10**7), rng.randrange(1, 10**10), wallet(rng.randrange(40)), t) for t in range(3_000)]
    for lamports, quote, payer, ts in feed:
        a.add("D", lamports, quote, payer, ts)
        b.add("D", lamports, quote, payer, ts)
    assert a.get("D") == b.get("D") and a.sample_wallets("D") == b.sample_wallets("D")
    assert a.get("D")["samples"] != [(lamports, quote) for lamports, quote, _p, _t in feed[:256]]


def test_a_payment_that_cannot_be_counted_is_left_out():
    pd = PayDests()
    pd.add("D", 0, 10**9, "p", 1)  # nothing paid
    pd.add("D", -5, 10**9, "p", 1)
    pd.add("D", 1_000, 0, "p", 1)  # no trade to compare it with
    pd.add("", 1_000, 10**9, "p", 1)
    assert len(pd) == 0 and pd.dirty == set()


# ---- C: one wallet does not decide a destination --------------------------------------------------------------------


def test_no_wallet_holds_more_than_a_tenth_of_a_sample():
    """A wallet at its cap that pays again replaces one of ITS OWN pairs, never another wallet's."""
    pd = PayDests()
    for j in range(6):  # forty wallets, six payments each, in turns
        for k in range(40):
            pd.add("D", 1_000 + k, 10**8 + j, wallet(k), 100 + k)
    assert len(pd.get("D")["samples"]) == 240 and set(pd.sample_wallets("D").values()) == {6}
    me = P.tag_of(wallet(0))
    for j in range(200):  # one of them goes on paying: under its cap it takes places as any payment does
        pd.add("D", 7, 10**9 + j, wallet(0), 1_000 + j)
    assert pd.sample_wallets("D")[me] == P.PER_PAYER_CAP == 25 == P.SAMPLES_CAP // P.MIN_PAYERS  # its cap, never more
    before = sorted(s for s in pd.get("D")["samples"] if s[0] not in (7, 1_000))
    assert len(before) == 256 - 25
    for j in range(200, 10_200):  # and ten thousand times more, at its cap
        pd.add("D", 7, 10**9 + j, wallet(0), 1_000 + j)
    after = pd.get("D")
    assert pd.sample_wallets("D")[me] == 25 and after["n"] == 10_440 and after["sample_payers"] == 40
    assert sorted(s for s in after["samples"] if s[0] not in (7, 1_000)) == before  # no other wallet's pair is touched
    newest = [q - 10**9 for lamports, q in after["samples"] if lamports == 7]
    assert len(newest) == 25 and max(newest) > 9_500 and min(newest) > 200  # its own pairs give way to its newer ones
    # a small sample: a tenth of it, three pairs at least, so one wallet cannot be most of a young destination's sample
    young = PayDests()
    for j in range(500):
        young.add("Y", 5, 10**8 + j, wallet(1), j)
    assert len(young.get("Y")["samples"]) == P.PER_PAYER_FLOOR == 3 and young.get("Y")["n"] == 500
    assert [P.payer_cap(size) for size in (0, 12, 29, 30, 99, 100, 249, 256)] == [3, 3, 3, 4, 10, 11, 25, 25]


def test_one_wallet_and_nine_passers_by_do_not_meet_the_bars():
    """Thirty payments and ten wallets were the first version's bars, and one wallet could make 21 of the 30. The
    payments that count are the pairs in the sample, of which a wallet holds a tenth."""
    pd = PayDests()
    for j in range(300):
        pd.add("D", 1_000_000, 10**8 * (1 + j % 50), wallet(0), j)
    for k in range(1, 10):
        pd.add("D", 1_000_000, 10**9 * k, wallet(k), 1_000 + k)
    pd.refresh(["D"], 2_000)
    pd.refresh(["D"], 2_001)
    d = pd.get("D")
    assert (d["n"], d["payers"], d["sample_payers"], len(d["samples"])) == (309, 10, 10, 12)
    assert d["kind"] is None


def test_one_wallet_cannot_turn_a_fee_account_into_a_tip_account():
    """The reviewers' reproduction (s4_poison.py b): a fee account of 300 payments, then ONE wallet sends it 1
    lamport beside dust trades of any size. The first version read "tip" after about 1,200 such payments (0.006 SOL
    of fees), and from then every user's one percent counted as a tip."""
    rng = random.Random(7)
    pd = PayDests()
    for i in range(300):
        q = int(0.3 * math.exp(rng.gauss(0, 1.2)) * SOL)
        pd.add("FEE", int(q * rng.choice((0.00987, 0.01023))), q, wallet(rng.randrange(60)), i)
    pd.refresh(["FEE"], 300)
    assert pd.get("FEE")["kind"] == "fee"
    kinds = set()
    for sent in range(1, 5_001):
        pd.add("FEE", 1, int(10 ** rng.uniform(5, 7)), "one-wallet", 300 + sent)
        if sent % 15 == 0:
            pd.refresh(["FEE"], 300 + sent)
            kinds.add(pd.get("FEE")["kind"])
    assert kinds == {"fee"}
    assert pd.sample_wallets("FEE")["one-wall"] <= P.PER_PAYER_CAP and not pd.get("FEE")["applied"]


def test_one_wallet_cannot_take_a_tip_accounts_kind_away():
    """The other way (s4_poison.py c): a tip account of 300 payments, then one wallet pays it one percent of its own
    dust trades, five thousand times. The first version stopped reading "tip" after a few hundred."""
    rng = random.Random(7)
    pd = PayDests()
    for i in range(300):
        q = int(0.3 * math.exp(rng.gauss(0, 1.2)) * SOL)
        pd.add("TIP", rng.choice((800_000, 1_000_000, 1_750_000)), q, wallet(rng.randrange(60)), i)
        if i in (149, 224, 299):
            pd.refresh(["TIP"], i)
    assert pd.get("TIP")["kind"] == "tip" and pd.get("TIP")["applied"]
    kinds, applied = set(), set()
    for sent in range(1, 5_001):
        q = int(10 ** rng.uniform(5, 7))
        pd.add("TIP", max(1, q // 100), q, "one-wallet", 300 + sent)
        if sent % 10 == 0:
            pd.refresh(["TIP"], 300 + sent)
            kinds.add(pd.get("TIP")["kind"])
            applied.add(pd.get("TIP")["applied"])
    assert kinds == {"tip"} and applied == {True}
    assert pd.sample_wallets("TIP")["one-wall"] == P.PER_PAYER_CAP


def test_one_wallet_spamming_a_young_tip_account_does_not_flip_it():
    """s3_reservoir.py part 4: twelve wallets tipped an account sixty times; then one wallet pays it one percent of
    its trades five thousand times. In the first version that wallet's pairs were 100 of the sample's 160 after a
    hundred payments and the account read "fee"."""
    rng = random.Random(3)
    pd = PayDests()
    for i in range(60):
        pd.add("TIP", rng.choice((800_000, 1_000_000, 1_750_000)), int(1e9 * 0.02 * 1000 ** rng.random()),
               wallet(i % 12), 1_000 + i)  # fmt: skip
        if i in (39, 59):
            pd.refresh(["TIP"], 1_000 + i)
    assert pd.get("TIP")["kind"] == "tip"
    kinds = set()
    for i in range(5_000):
        q = int(1e9 * 0.02 * 1000 ** rng.random())
        pd.add("TIP", q // 100, q, "spammer", 3_000 + i)
        if i % 50 == 49:
            pd.refresh(["TIP"], 4_000 + i)
            kinds.add(pd.get("TIP")["kind"])
    d = pd.get("TIP")
    assert kinds == {"tip"} and d["n"] == 5_060
    assert pd.sample_wallets("TIP")["spammer"] <= len(d["samples"]) // 10 + 1


# ---- D and B: two readings before "tip", a higher bar before it is applied -------------------------------------------


def feed(pd: PayDests, dest: str, rng: random.Random, wallets: int, n: int, start: int = 0, flat: bool = True) -> None:
    for k, q in enumerate(trades(rng, n)):
        pd.add(dest, 1_000_000 if flat else q // 100, q, wallet(k % wallets), start + k)


def test_the_first_tip_needs_two_readings_in_a_row():
    pd = PayDests()
    rng = random.Random(30)
    feed(pd, "T", rng, wallets=40, n=120)
    pd.refresh(["T"], now=1_000)
    first = pd.get("T")
    assert (first["kind"], first["tip_streak"], first["tip_since"], first["rho"]) == (None, 1, None, 0.0)
    pd.refresh(["T"], now=2_000)
    second = pd.get("T")
    assert (second["kind"], second["tip_streak"], second["tip_since"], second["applied"]) == ("tip", 2, 2_000, False)
    assert pd.tips() == {"T": 2_000} and pd.applied() == set()
    assert P.TIP_CONFIRM == 2
    # a reading that is not "tip" between them starts the count again
    other = PayDests()
    feed(other, "T", random.Random(30), wallets=40, n=120)
    other.refresh(["T"], now=1_000)
    feed(other, "T", random.Random(31), wallets=40, n=60_000, start=200, flat=False)  # it turns into a fee account
    other.refresh(["T"], now=2_000)
    assert (other.get("T")["kind"], other.get("T")["tip_streak"]) == ("fee", 0)


def test_applied_needs_twenty_five_wallets_and_tip_at_two_flushes_in_a_row():
    """B: listing and applying are two bars. The table calls an account "tip" from 30 payments by 10 wallets at two
    readings; a trade's tip is read with it only once 25 different wallets are in its sample and it has been "tip"
    at two flushes in a row."""
    pd = PayDests()
    feed(pd, "FEW", random.Random(32), wallets=24, n=300)
    feed(pd, "MANY", random.Random(33), wallets=25, n=300)
    feed(pd, "YOUNG", random.Random(34), wallets=40, n=200)  # wallets enough, its sample not yet full
    states = []
    for now in (1, 2, 3, 4):
        pd.refresh(["FEW", "MANY", "YOUNG"], now)
        states.append([(pd.get(a)["kind"], pd.get(a)["applied"]) for a in ("FEW", "MANY", "YOUNG")])
    assert states == [
        [(None, False), (None, False), (None, False)],  # the first reading as a tip
        [("tip", False), ("tip", False), ("tip", False)],  # the second: "tip" in the table
        [("tip", False), ("tip", True), ("tip", False)],  # "tip" at two flushes in a row: applied, with 25 wallets
        [("tip", False), ("tip", True), ("tip", False)],  # 24 wallets never are, nor 200 pairs
    ]
    assert pd.get("FEW")["sample_payers"] == 24 and pd.applied() == {"MANY"}
    assert (P.APPLY_MIN_PAYERS, P.APPLY_KEEP_PAYERS, P.APPLY_MIN_N) == (25, 20, 256)
    assert [r["applied"] for r in pd.rows(["FEW", "MANY"])] == [0, 1]
    feed(pd, "YOUNG", random.Random(35), wallets=40, n=100, start=500)  # its sample fills: applied at the next flush
    pd.refresh(["YOUNG"], 5)
    assert pd.get("YOUNG")["applied"] and len(pd.get("YOUNG")["samples"]) == 256


def test_an_operators_ten_wallets_tipping_their_own_account_are_listed_and_never_applied():
    """The reviewers' reproduction (s4c_selftip.py): ten wallets of one operator send 0.05 to 0.12 SOL, drawn apart
    from the trade's size, to an account of their own with every trade. Amounts cannot tell that from a landing
    service: the table reads "tip" (10 wallets is its bar), and it is never applied to a trade's tip (25 is that
    bar), so no 0.085 SOL a trade is ever written as a bid."""
    rng = random.Random(11)
    pd = PayDests()
    applied = False
    for k in range(4_000):
        pd.add("OWN", int(SOL * rng.uniform(0.05, 0.12)), int(SOL * 0.3 * 10 ** rng.random()), wallet(k % 10), k)
        if (k + 1) % 20 == 0:
            pd.refresh(["OWN"], k)
            applied = applied or pd.get("OWN")["applied"]
    d = pd.get("OWN")
    assert (d["kind"], d["sample_payers"], applied) == ("tip", 10, False)
    assert len(d["samples"]) == 250  # ten wallets, twenty-five pairs each


def test_an_applied_account_is_not_dropped_for_one_wallet_less():
    """Applied at 25 wallets in the sample, kept down to 20: an account at the bar is not applied one flush and left
    out the next. Under 20, or no longer "tip", it is not applied."""
    pd = PayDests()
    feed(pd, "T", random.Random(34), wallets=25, n=300)
    for now in (1, 2, 3):
        pd.refresh(["T"], now)
    assert pd.get("T")["applied"] and pd.get("T")["sample_payers"] == 25
    d = pd._dests["T"]
    for gone in range(5):  # five of its wallets leave the sample (their pairs taken by other wallets' newer ones)
        for i, tag in enumerate(d.tags):
            if tag == P.tag_of(wallet(gone)):
                d.tags[i] = P.tag_of(wallet(24))
        d.count[P.tag_of(wallet(24))] = d.count.get(P.tag_of(wallet(24)), 0) + d.count.pop(P.tag_of(wallet(gone)))
    pd.refresh(["T"], 4)
    assert pd.get("T")["sample_payers"] == 20 and pd.get("T")["applied"]
    d.count[P.tag_of(wallet(24))] += d.count.pop(P.tag_of(wallet(5)))
    d.tags[:] = [P.tag_of(wallet(24)) if t == P.tag_of(wallet(5)) else t for t in d.tags]
    pd.refresh(["T"], 5)
    assert pd.get("T")["sample_payers"] == 19 and not pd.get("T")["applied"] and pd.get("T")["kind"] == "tip"


# ---- E: room, and what is never dropped ------------------------------------------------------------------------------


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


def test_a_learned_tip_account_is_never_dropped_for_room():
    """The reviewers' reproduction (s5_evict.py): a tip account, well learned, is not paid for an hour while other
    destinations two wallets each paid come by. In the first version it was the longest unseen and went."""
    rng = random.Random(2)
    pd = PayDests(max_dests=100)
    feed(pd, "TIP", rng, wallets=30, n=300, start=10)
    for now in (200, 201, 202):
        pd.refresh(["TIP"], now)
    feed(pd, "LISTED", rng, wallets=12, n=60, start=10)  # read as tip in the table, too few wallets to be applied
    pd.refresh(["LISTED"], 200)
    pd.refresh(["LISTED"], 201)
    assert pd.get("TIP")["applied"] and (pd.get("LISTED")["kind"], pd.get("LISTED")["applied"]) == ("tip", False)
    for d in range(400):
        for w in range(2):
            pd.add(f"other{d}", 5_000, 10**8, wallet(1_000 + 2 * d + w), 5_000 + d)
    assert len(pd) <= 100 and pd.dropped >= 300
    assert "TIP" in pd and "LISTED" in pd and pd.get("TIP")["n"] == 300


# ---- the store's rows ------------------------------------------------------------------------------------------------


def _learn(pd: PayDests, rng: random.Random, start: int = 0) -> None:
    for k, q in enumerate(trades(rng, 400)):
        payer = wallet(k % 25)
        pd.add("TIPACC", 1_000_000, q, payer, start + k)
        pd.add("FEEACC", q // 100, q, payer, start + k)
        if k < 20:
            pd.add("YOUNG", 1_000_000, q, payer, start + k)


def test_refresh_classifies_what_changed_and_remembers_since_when():
    pd = PayDests()
    _learn(pd, random.Random(10))
    assert pd.dirty == {"TIPACC", "FEEACC", "YOUNG"}
    assert pd.refresh(pd.dirty, now=4_000) == 3
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
        pd.add("TIPACC", q // 100, q, wallet(k % 25), 10_000 + k)
    pd.refresh({"TIPACC"}, now=80_000)
    d = pd.get("TIPACC")
    assert (d["kind"], d["tip_since"], d["applied"], d["tip_streak"]) == ("fee", None, False, 0) and pd.tips() == {}


def test_rows_go_to_the_store_and_load_brings_the_learning_back():
    pd = PayDests()
    _learn(pd, random.Random(12))
    for now in (3_000, 4_000, 5_000):
        pd.refresh(pd.dirty, now=now)
    rows = pd.rows()
    assert {r["address"] for r in rows} == {"TIPACC", "FEEACC", "YOUNG"}
    assert [r["address"] for r in pd.rows({"FEEACC", "GONE"})] == ["FEEACC"]
    tip = next(r for r in rows if r["address"] == "TIPACC")
    assert set(tip) == set(P.ROW_FIELDS) == {
        "address", "kind", "n", "payers", "rho", "median_lamports", "median_pct", "first_ts", "last_ts",
        "tip_since", "samples", "applied", "tip_streak", "sample_payers"}  # fmt: skip
    assert (tip["applied"], tip["tip_streak"], tip["sample_payers"], tip["tip_since"]) == (1, 3, 25, 4_000)
    stored = json.loads(tip["samples"])
    # the sample: its wallets once, by their tags, then lamports, quote and the wallet's place for each pair
    assert sorted(stored) == ["p", "w"] and len(stored["w"]) == 25 and len(stored["p"]) == 3 * 256
    assert all(len(w) == 8 for w in stored["w"]) and max(stored["p"][2::3]) == 24
    back = PayDests()
    assert back.load(rows) == 3
    assert back.dirty == set()  # what was loaded is what the table holds
    for address in ("TIPACC", "FEEACC", "YOUNG"):
        assert back.get(address) == pd.get(address) and back.sample_wallets(address) == pd.sample_wallets(address)
    assert back.tips() == {"TIPACC": 4_000} and back.applied() == {"TIPACC"}
    # and it goes on from there: the same payments after the restart give the same state as without one
    _learn(pd, random.Random(13), start=1_000)
    _learn(back, random.Random(13), start=1_000)
    pd.refresh(pd.dirty, now=7_000)
    back.refresh(back.dirty, now=7_000)
    for address in ("TIPACC", "FEEACC", "YOUNG"):
        assert back.get(address) == pd.get(address)
    assert back.get("YOUNG")["n"] == 40 and back.get("TIPACC")["tip_since"] == 4_000


def test_after_a_restart_the_wallets_of_the_stored_sample_still_count():
    """D9: nine wallets paid it before the restart and are in the stored sample; one new wallet after it makes ten.
    (The first version kept only the number nine and counted the wallets again from none.)"""
    pd = PayDests()
    rng = random.Random(40)
    for k, q in enumerate(trades(rng, 45)):
        pd.add("D", 1_000_000, q, wallet(k % 9), k)
    pd.refresh(["D"], 100)
    assert (pd.get("D")["payers"], pd.get("D")["sample_payers"], pd.get("D")["tip_streak"]) == (9, 9, 0)
    back = PayDests()
    back.load(pd.rows())
    back.add("D", 1_000_000, 3 * SOL, wallet(0), 200)  # a wallet it knows: still nine
    assert (back.get("D")["payers"], back.get("D")["sample_payers"]) == (9, 9)
    back.add("D", 1_000_000, 5 * SOL, wallet(9), 201)  # a new one: ten
    assert (back.get("D")["payers"], back.get("D")["sample_payers"]) == (10, 10)
    for k, q in enumerate(trades(rng, 20)):  # ten wallets fill the sample between them: thirty pairs and more
        back.add("D", 1_000_000, q, wallet(k % 10), 210 + k)
    back.refresh(["D"], 300)
    back.refresh(["D"], 301)
    assert back.get("D")["kind"] == "tip" and len(back.get("D")["samples"]) >= 30


def test_a_row_the_first_version_wrote_still_loads():
    """Round 1 stored the sample as a flat list lamports, quote, ... without wallets. Such a row loads with its
    count, its kind and its pairs; the pairs are one unknown wallet's, so the wallet bars are met again by wallets
    seen from now on, and until then it is not applied."""
    old = {"address": "A", "kind": "tip", "n": 400, "payers": 40, "rho": 0.0, "median_lamports": 5, "median_pct": 0.1,
           "first_ts": 1, "last_ts": 2, "tip_since": 2, "samples": "[5, 100, 5, 200, 5, 900]"}  # fmt: skip
    pd = PayDests()
    assert pd.load([old]) == 1
    d = pd.get("A")
    assert (d["kind"], d["n"], d["payers"], d["sample_payers"]) == ("tip", 400, 40, 1)
    assert d["samples"] == [(5, 100), (5, 200), (5, 900)]
    assert d["applied"] is False and pd.sample_wallets("A") == {"?": 3}
    pd.refresh(["A"], 10)
    assert pd.get("A")["kind"] is None  # one unknown wallet: the bars are not met


def test_a_bad_row_is_left_out_of_a_load_not_the_whole_table():
    pd = PayDests()
    good = {"address": "A", "kind": "tip", "n": 40, "payers": 12, "rho": 0.0, "median_lamports": 5, "median_pct": 0.1,
            "first_ts": 1, "last_ts": 2, "tip_since": 2, "applied": 1, "tip_streak": 4,
            "samples": '{"w":["aaaaaaaa","bbbbbbbb"],"p":[5,100,0,5,200,1]}'}  # fmt: skip
    rows = [
        good,
        {**good, "address": "B", "samples": "not json"},
        {**good, "address": "C", "n": None},
        {"address": "D"},
        {**good, "address": "E", "samples": None},
        {**good, "address": ""},
        {**good, "address": "F", "samples": '{"w":["a"],"p":[5,100,7]}'},
    ]  # a pair naming a wallet not listed
    assert pd.load(rows) == 2
    assert pd.get("A")["samples"] == [(5, 100), (5, 200)] and pd.get("E")["samples"] == [] and pd.get("B") is None
    assert pd.sample_wallets("A") == {"aaaaaaaa": 1, "bbbbbbbb": 1}
    assert pd.tips() == {"A": 2, "E": 2} and pd.applied() == {"A", "E"} and pd.get("A")["tip_streak"] == 4


def test_a_load_takes_every_learned_account_whatever_the_room():
    """E: memory has room for so many destinations; a row read as a tip account or applied is taken beyond it."""
    row = {"kind": None, "n": 40, "payers": 12, "rho": 0.0, "median_lamports": 5, "median_pct": 0.1, "first_ts": 1,
           "last_ts": 2, "tip_since": None, "samples": "[]"}  # fmt: skip
    rows = [{**row, "address": f"PLAIN{k}"} for k in range(5)]
    rows += [{**row, "address": "TIP", "kind": "tip", "tip_since": 2}, {**row, "address": "APPLIED", "kind": "tip",
                                                                         "applied": 1, "tip_since": 2}]  # fmt: skip
    pd = PayDests(max_dests=3)
    assert pd.load(rows) == 5
    assert "TIP" in pd and "APPLIED" in pd and sum(1 for k in range(5) if f"PLAIN{k}" in pd) == 3


def test_a_destination_seen_again_goes_on_from_its_stored_row():
    """E: a destination that came into memory by a payment (dropped for room before, or never loaded because the
    table could not be read at the start) is brought together with its stored row before it is read and written:
    the stored count, sample and reading, with the payments memory counted since on top. The first version wrote
    its three new payments over a row of six hundred."""
    old = PayDests()
    feed(old, "TIP", random.Random(50), wallets=30, n=600)
    for now in (1, 2, 3):
        old.refresh(["TIP"], now)
    stored = old.rows(["TIP"])
    assert stored[0]["applied"] == 1 and stored[0]["n"] == 600

    young = PayDests()  # the next process, its load failed
    for k in range(3):
        young.add("TIP", 1_000_000, (k + 1) * SOL, wallet(100 + k), 5_000 + k)
    young.add("NEW", 1_000_000, SOL, wallet(1), 5_000)
    assert young.unsynced(["TIP", "NEW", "NOBODY"]) == ["TIP", "NEW"]
    assert young.merge(stored, checked=["TIP", "NEW"]) == 1
    d = young.get("TIP")
    assert (d["n"], d["kind"], d["applied"], d["tip_streak"], d["first_ts"], d["last_ts"]) == (603, "tip", True, 3,
                                                                                               0, 5_002)  # fmt: skip
    assert d["payers"] == 33 and 30 <= d["sample_payers"] <= 33  # a full sample takes a new pair by its chance
    assert young.unsynced(["TIP", "NEW"]) == []  # checked once: memory and the table go on together
    young.refresh(["TIP"], 6_000)
    assert young.get("TIP")["applied"] and young.get("TIP")["kind"] == "tip"
    assert young.merge(stored, checked=["TIP"]) == 0 and young.get("TIP")["n"] == 603  # never twice
    loaded = PayDests()
    loaded.load(stored)
    assert loaded.unsynced(["TIP"]) == []  # a loaded destination needs no check


# ---- the module itself -----------------------------------------------------------------------------------------------


def test_what_amounts_cannot_decide_is_said_in_plain_words():
    text = " ".join(P.NOT_DECIDED_BY_AMOUNTS)
    said = ["who owns the account", "percentage of the buy reads as fee", "follows the trader's size"]
    said += ["a floor that nearly every trade hits can read as tip", "one size with one tip", "half the trade"]
    for words in said:
        assert words in text
    assert all(words in P.__doc__ for words in ("percentage of the buy", "trader's size", "floor"))


def test_the_module_is_pure_and_light():
    """No clock, no file, no network, and nothing heavy to load: the tape recorder imports it."""
    import subprocess
    import sys

    code = ("import sys\nimport snipetech.solana.pay_dests as P\n"
            "print(sorted(m for m in ('numpy', 'time', 'random', 'sqlite3', 'httpx') if m in vars(P)))\n"
            "print('numpy' in sys.modules)")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split("\n")
    assert out[:2] == ["[]", "False"]

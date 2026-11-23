"""The lab's text report says what a wallet rule's ceiling on our own fill did (23 Nov 2026).

A rule with `max_fill_mcap` counts each send its ceiling refused as a copy that cost 0.001005 SOL (lab.REFUSED_SEND_SOL)
and carries the counts in `fill_ceiling`; the by-wallet rows carry `refused`. The text report showed neither, so a
reader of the report saw a mean pulled toward zero by refusals with nothing to say so, and its band table still named
`max_mcap`, a gate on the leader's buy that no OKTech setting runs, as the market-cap gate."""

from snipetech import report as R
from tests.test_lab_mig_skip import _ranked

LAB = {"moments": 30, "tape_hours": 10.0, "buy_sol": 0.2, "latency_slots": 1, "position": "+3"}
BLOCK = {"max_fill_mcap": 64.6, "admitted": 20, "refused": 10, "refused_send_sol": 0.001005,
         "test": {"admitted": 8, "refused": 4, "admitted_mean": 0.031}, "note": "..."}  # fmt: skip


def test_the_lab_report_says_how_many_sends_a_rules_ceiling_on_our_own_fill_refused():
    ranked = [r | {"landing": "+3@1"} for r in _ranked([(0.01, 0.02) for _ in range(30)])]
    ranked[0] = ranked[0] | {"fill_ceiling": BLOCK}
    text = R.render_lab(LAB | {"strategies": ranked})
    line = next(x for x in text.splitlines() if "ceiling on our own fill" in x)
    assert "64.6 SOL" in line and "20 admitted, 10 refused" in line and "0.001005 SOL" in line
    assert "8 admitted, 4 refused" in line and "+0.0310" in line  # the newer 40 %: what its test numbers are made of
    plain = R.render_lab(LAB | {"strategies": [r | {"landing": "+3@1"} for r in _ranked([(0.01, 0.02)] * 30)]})
    assert "ceiling on our own fill" not in plain


def test_the_wallet_table_shows_the_refused_sends_when_a_ceiling_refused_any():
    ranked = [r | {"landing": "+3@1"} for r in _ranked([(0.01, 0.02) for _ in range(30)])]
    row = {"label": "Cented", "wallet": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o", "trigger": "kol cand",
           "exit": "hold12", "n": 30, "mean": 0.01, "ev_lower": 0.005, "win_rate": 0.6}  # fmt: skip
    text = R.render_lab(LAB | {"strategies": ranked, "wallets": [row | {"refused": 7}]})
    head = next(x for x in text.splitlines() if x.lstrip().startswith("label"))
    assert "refused" in head
    assert "refused" not in next(x for x in R.render_lab(LAB | {"strategies": ranked, "wallets": [row]}).splitlines()
                                 if x.lstrip().startswith("label"))  # fmt: skip

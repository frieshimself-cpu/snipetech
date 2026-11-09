"""The shadow report: each of our would-be sales set against OKTech's real sale of the same coin (our wallet's next sell
on the tape): who sold first, and at what price."""

import json
import time

from snipetech.exitrun.run import shadow_report
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
W = addr("rep-wallet")


def test_report_pairs_a_shadow_decision_with_oktechs_actual_sale(tmp_path):
    st = Store(":memory:")
    try:
        m = Market(st, addr("rep-coin"), addr("rep-dev"), NOW - 300, 60_000_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(W, 0.2, 2)
        m.buy(addr("fan"), 2.0, 5)
        m.sell(W, 1.0, 40)  # OKTech's sale, 16 s after the buy, after the price rose
        m.commit()
        sale = next(t for t in st.trades_for_mint(addr("rep-coin")) if t.user == W and not t.is_buy)
        log = tmp_path / "exitrun.jsonl"
        log.write_text(json.dumps({"mode": "shadow", "mint": addr("rep-coin"), "reason": "timer", "frac": 1.0,
                                   "price": 1e-7, "entry_price": 1e-7, "decided_at": float(sale.ts) - 5,
                                   "trade_ts": None}) + "\n")  # fmt: skip
        rep = shadow_report(st, W, log)
        (row,) = rep["rows"]
        assert row["reason"] == "timer" and row["ours_first_by_s"] == 5.0
        assert row["oktech_price"] > 0 and row["shadow_better_pct"] is not None
        assert rep["n"] == 1
    finally:
        st.close()


def test_cli_exitrun_report_and_the_ops_channel(tmp_path, capsys):
    from snipetech import cli
    from snipetech.ops import ALLOWED

    assert "exitrun" in ALLOWED
    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    Store(db).close()
    assert cli.main(["--db", str(db), "exitrun", "report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n"] == 0

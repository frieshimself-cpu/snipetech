"""Launch tools by the mark they leave in a create transaction (owner, 10 Nov: "there's a tool called proxima that
people deploy through, and when they do that the launches are usually organised and go high ... is there a way for it
to automatically detect proxima?"). A tool pays itself a small fee inside the launch, wraps the create in its own
program, or signs the launch for the creator. Launches grouped by mark, with how far each mark's coins run, show the
tools worth targeting whatever their names."""

import json
import time
from pathlib import Path

from snipetech.analysis.launch_tools import tool_table, tx_marks
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
RM = json.loads((Path(__file__).parent / "fixtures" / "launch_tools" / "rm_create.json").read_text())
SIGNER = "Bjr89zvdsKjferx1uYxcZ9kywqK4zE8PVzAUbHGXBHJr"
CREATOR = "5JE1ijBKFi8hJ2nGct2ZjKh6w4wirn9WrsSUfrLoqH4A"


def test_a_real_tool_launch_carries_the_tools_fee_wallet_and_its_signer_and_not_the_tip():
    """RM, 6 Nov: a direct pump.fun create and buy signed by Bjr89z for creator 5JE1ij, 0.0025 SOL to moonKfft (the
    tool's fee) and 0.0085 SOL to a Nozomi tip account (how it was sent, not who launched it)."""
    marks = tx_marks(RM, CREATOR)
    assert "fee:moonKfftMiGSak3cezvhEqvkPSzwrmQxQHXuspC96yj" in marks and f"payer:{SIGNER}" in marks
    assert not any(m.startswith(("program:", "fee:noz")) for m in marks) and len(marks) == 2
    assert tx_marks(RM, SIGNER) == ["fee:moonKfftMiGSak3cezvhEqvkPSzwrmQxQHXuspC96yj"]  # its own launch: no payer mark


def _coin(st, i, *, run):
    m = Market(st, addr(f"lt-coin-{i}"), addr(f"lt-dev-{i}"), NOW - 3000 + i * 10, 90_000_000 + i * 100)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("early"), 1.0, 1)
    if run:
        m.buy(addr("crowd"), 20.0, 5)
        m.buy(addr("whale"), 30.0, 100)
    else:
        m.sell(addr("early"), 1.0, 5)
    m.commit()
    return st.launch(addr(f"lt-coin-{i}"))


def test_launches_are_grouped_by_mark_with_how_far_each_marks_coins_run():
    st = Store(":memory:")
    try:
        tool = [(_coin(st, i, run=True), ["fee:TOOL"]) for i in range(3)]
        plain = [(_coin(st, 10 + i, run=False), []) for i in range(4)]
        rare = [(_coin(st, 20, run=True), ["fee:RARE", "fee:TOOL"])]
        out = tool_table(tool + plain + rare, st.trades_for_mint, min_launches=3, horizons=(13, 150))
        assert out["launches"] == 8 and out["all"]["launches"] == 8 and out["all"]["reached_30"] == 0.5
        rows = {r["mark"]: r for r in out["marks"]}
        assert set(rows) == {"fee:TOOL", "none"}  # a mark seen once is not a tool
        t, p = rows["fee:TOOL"], rows["none"]
        assert t["launches"] == 4 and t["creators"] == 4 and t["reached_30"] == 1.0 and t["traded"] == 1.0
        assert p["launches"] == 4 and p["reached_30"] == 0.0 and p["peak_sol"] < 2 and t["peak_sol"] > 50
        assert t["ret_13"] > 50 and p["ret_13"] < 0 and t["win_150"] == 1.0 and p["win_150"] == 0.0
        assert len(t["examples"]) == 3 and t["examples"][0]["mint"].startswith("") and "peak_sol" in t["examples"][0]
        assert out["marks"][0]["mark"] == "fee:TOOL"  # marks first by the share of their coins that ran
    finally:
        st.close()


def test_the_agents_may_start_it():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "launch-tools" in RESEARCH_COMMANDS and "launch-tools" in ALLOWED and "launch-tools" not in OWNER_COMMANDS

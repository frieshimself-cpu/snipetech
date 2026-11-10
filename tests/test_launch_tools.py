"""Launch tools by the mark they leave in a create transaction (owner, 10 Nov: "there's a tool called proxima that
people deploy through, and when they do that the launches are usually organised and go high ... is there a way for it
to automatically detect proxima?"). A tool pays itself a small fee inside the launch, wraps the create in its own
program, or signs the launch for the creator. Launches grouped by mark, with how far each mark's coins run, show the
tools worth targeting whatever their names."""

import asyncio
import json
import time
from pathlib import Path

from snipetech.analysis.launch_tools import coin_marks, mark_split, tool_table, tx_marks
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
RM = json.loads((Path(__file__).parent / "fixtures" / "launch_tools" / "rm_create.json").read_text())
CALLINU = json.loads((Path(__file__).parent / "fixtures" / "launch_tools" / "callinu_create.json").read_text())
PROXIMA_TABLE = "alt:Hyif6eWb8x88RVrvjPfabsgRYnwkVnyByEXTVTXbUcyP"
CALLINU_MINT = "58EqSxVamSPQH56cfaMU9mxvvsV3nfgXYe6QEsKDpump"
CALLINU_DEV = "EYbH2PN8rGUpRWaRHJZ5v27Z7k6isdLk2RFKSD173kDk"
SIGNER = "Bjr89zvdsKjferx1uYxcZ9kywqK4zE8PVzAUbHGXBHJr"
CREATOR = "5JE1ijBKFi8hJ2nGct2ZjKh6w4wirn9WrsSUfrLoqH4A"


def test_a_real_tool_launch_carries_the_tools_fee_wallet_and_its_signer_and_not_the_tip():
    """RM, 6 Nov: a direct pump.fun create and buy signed by Bjr89z for creator 5JE1ij, 0.0025 SOL to moonKfft (the
    tool's fee) and 0.0085 SOL to a Nozomi tip account (how it was sent, not who launched it)."""
    marks = tx_marks(RM, CREATOR)
    assert "fee:moonKfftMiGSak3cezvhEqvkPSzwrmQxQHXuspC96yj" in marks and f"payer:{SIGNER}" in marks
    assert not any(m.startswith(("program:", "fee:noz")) for m in marks) and len(marks) == 2
    assert tx_marks(RM, SIGNER) == ["fee:moonKfftMiGSak3cezvhEqvkPSzwrmQxQHXuspC96yj"]  # its own launch: no payer mark


def test_a_tools_address_lookup_table_is_a_mark():
    """Callout Inu, 5 Nov, one of six coins the owner named as Proxima deploys: no fee, no wrapper, signed by its own
    creator; what the tool leaves is the address lookup table its transactions load."""
    assert tx_marks(CALLINU, CALLINU_DEV) == [PROXIMA_TABLE]
    assert not any(m.startswith("alt:") for m in tx_marks(RM, CREATOR))


class _Rpc:
    """Signatures by mint, newest first, two to a page; the oldest is its create."""

    def __init__(self, sigs, txs):
        self.sigs, self.txs, self.pages = sigs, txs, 0

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        self.pages += 1
        sigs = self.sigs.get(address, [])
        start = sigs.index(before) + 1 if before else 0
        return [{"signature": s, "slot": 1} for s in sigs[start : start + 2]]

    async def get_transactions(self, sigs):
        return {s: self.txs[s] for s in sigs if s in self.txs}


def test_named_coins_say_which_mark_is_the_tools():
    """The owner names coins a tool launched; the mark they all carry is the tool's. A coin on the tape is read from
    its recorded create; an older one from the first transaction that ever named its mint."""
    st = Store(":memory:")
    try:
        on_tape = _coin(st, 30, run=True)
        sigs = {CALLINU_MINT: ["s5", "s4", "s3", "s2", "create-sig"]}
        rpc = _Rpc(sigs, {on_tape.sig: RM, "create-sig": CALLINU})
        out = asyncio.run(coin_marks(rpc, st, [on_tape.mint, CALLINU_MINT, addr("never-launched")]))
        a, b, c = out["coins"]
        assert (
            a["on_tape"] and a["sig"] == on_tape.sig and "fee:moonKfftMiGSak3cezvhEqvkPSzwrmQxQHXuspC96yj" in a["marks"]
        )
        assert not b["on_tape"] and b["sig"] == "create-sig" and b["creator"] == CALLINU_DEV
        assert b["marks"] == [PROXIMA_TABLE] and rpc.pages == 5  # 3 pages of 2 and the empty one; one for (c)
        assert c["sig"] is None and c["marks"] == [] and c["error"]
        assert out["shared"] == []  # the two coins found carry no common mark
        both = asyncio.run(coin_marks(_Rpc(sigs, {"create-sig": CALLINU}), st, [CALLINU_MINT, CALLINU_MINT]))
        assert both["shared"] == [PROXIMA_TABLE]
    finally:
        st.close()


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


def test_a_marks_launches_are_split_by_who_bought_in_the_launch_block():
    """Proxima's table is on one launch in nine and most of those die; the coins the owner named had four wallets
    buying about 10 SOL in the create's own block. The split says which launches of a mark run, and lists them."""
    st = Store(":memory:")
    try:
        organised = []
        for i in range(3):
            m = Market(st, addr(f"ms-org-{i}"), addr(f"ms-dev-{i}"), NOW - 4000 + i * 10, 91_000_000 + i * 100)
            m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
            for w in range(4):
                m.buy(addr(f"ms-team-{i}-{w}"), 2.5, 0)
            m.buy(addr("crowd"), 20.0, 5)
            m.buy(addr("whale"), 30.0, 100)
            m.commit()
            organised.append((st.launch(m.mint), ["alt:T"]))
        solo = [(_coin(st, 40 + i, run=False), ["alt:T", "fee:Y"]) for i in range(2)]
        other = [(_coin(st, 50, run=True), ["fee:X"])]
        out = mark_split(organised + solo + other, st.trades_for_mint, "alt:T", horizons=(13, 150))
        assert out["mark"] == "alt:T" and out["launches"] == 5
        w = {r["bucket"]: r for r in out["by"]["block_wallets"]}
        assert w["4+"]["launches"] == 3 and w["4+"]["reached_30"] == 1.0 and w["4+"]["ret_150"] > 50
        assert w["0"]["launches"] == 2 and w["0"]["reached_30"] == 0.0 and "1" not in w  # empty buckets are left out
        sol = {r["bucket"]: r for r in out["by"]["block_sol"]}
        assert sol["10+"]["launches"] == 3 and sol["0"]["launches"] == 2
        dev = {r["bucket"]: r for r in out["by"]["dev_sol"]}
        assert dev["1-2"]["launches"] == 3 and dev["0.5-1"]["launches"] == 2
        coins = {c["mint"]: c for c in out["coins"]}
        c = coins[addr("ms-org-1")]
        assert c["block_wallets"] == 4 and round(c["block_sol"], 1) == 10.0 and round(c["dev_sol"], 1) == 1.0
        assert c["peak_sol"] > 50 and c["ret_13"] > 0 and c["creator"] == addr("ms-dev-1") and c["ts"] == NOW - 3990
        assert [x["ts"] for x in out["coins"]] == sorted((x["ts"] for x in out["coins"]), reverse=True)  # newest first
        assert len(mark_split(organised + solo, st.trades_for_mint, "alt:T", max_coins=2)["coins"]) == 2
    finally:
        st.close()


def test_the_agents_may_start_it():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "launch-tools" in RESEARCH_COMMANDS and "launch-tools" in ALLOWED and "launch-tools" not in OWNER_COMMANDS


def test_a_launchs_marks_are_kept_and_a_launch_with_none_is_remembered_as_read():
    st = Store(":memory:")
    try:
        assert st.launch_marks_of(["a", "b"]) == {}
        assert st.put_launch_marks({"a": ["fee:X", PROXIMA_TABLE], "b": []}) == 2
        assert st.launch_marks_of(["a", "b", "c"]) == {"a": sorted(["fee:X", PROXIMA_TABLE]), "b": []}
        st.put_launch_marks({"a": ["fee:X", PROXIMA_TABLE]})  # written twice: still one of each
        assert st.launch_marks_of(["a"]) == {"a": sorted(["fee:X", PROXIMA_TABLE])}
    finally:
        st.close()


class _CountingRpc:
    def __init__(self):
        self.fetched = 0

    async def get_transactions(self, sigs):
        self.fetched += len(sigs)
        return dict.fromkeys(sigs, CALLINU)


def test_a_second_scan_reads_the_marks_from_the_database_and_fetches_only_new_launches():
    """10 Nov: a 24 h scan is 30,000 getTransaction calls and a quarter of an hour, and the owner's page wants it
    every hour. A launch's marks never change: once read they are kept, and the next scan fetches the new launches."""
    from snipetech.analysis.launch_tools import launch_tools

    st = Store(":memory:")
    try:
        for i in range(6):
            _coin(st, 60 + i, run=i % 2 == 0)
        rpc = _CountingRpc()
        first = asyncio.run(launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st, mark=PROXIMA_TABLE))
        assert rpc.fetched == 6 and first["launches"] == 6 and first["fetched"] == 6
        assert first["split"]["launches"] == 6 and first["marks"][0]["mark"] == PROXIMA_TABLE
        _coin(st, 70, run=True)
        again = asyncio.run(launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st, mark=PROXIMA_TABLE))
        assert rpc.fetched == 7 and again["launches"] == 7 and again["fetched"] == 1
        plain = asyncio.run(launch_tools(rpc, st, NOW, hours=12, min_launches=1))  # no marks store: every one fetched
        assert rpc.fetched == 14 and plain["launches"] == 7
    finally:
        st.close()


FUNDING = json.loads((Path(__file__).parent / "fixtures" / "launch_tools" / "proxima_funding.json").read_text())


def test_a_wallets_first_funding_by_a_bare_transfer_with_a_set_compute_limit_is_a_mark():
    """Proxima, 10 Nov: the create is pump.fun's standard path, on 4,000 launches a day. What its launches share is
    how the wallets are funded minutes before: a transfer from a one-off wallet with setComputeUnitLimit(1450) and
    nothing else (14 of 14 wallets around the owner's coins, 1 of 40 creators of other launches)."""
    from snipetech.analysis.launch_tools import funding_mark

    assert funding_mark(FUNDING) == "fund:cu1450"
    assert funding_mark(RM) is None and funding_mark(CALLINU) is None  # a launch is not a funding


class _FundingRpc:
    """Each creator's only earlier transaction is the Proxima funding; every create is CALLINU's."""

    def __init__(self, funded):
        self.funded, self.tx_calls, self.sig_calls = set(funded), 0, 0

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        self.sig_calls += 1
        if before:
            return []
        return [{"signature": f"fund-{address}"}] if address in self.funded else [{"signature": f"other-{address}"}]

    async def get_transactions(self, sigs):
        self.tx_calls += len(sigs)
        return {s: (FUNDING if s.startswith("fund-") else RM if s.startswith("other-") else CALLINU) for s in sigs}


def test_the_scan_reads_the_funding_of_a_marks_creators_once_and_counts_the_launches_with_it():
    from snipetech.analysis.launch_tools import launch_tools

    st = Store(":memory:")
    try:
        coins = [_coin(st, 80 + i, run=i < 2) for i in range(5)]
        rpc = _FundingRpc([c.creator for c in coins[:2]])
        out = asyncio.run(launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st,
                                       funding_of=PROXIMA_TABLE, mark="fund:cu1450"))  # fmt: skip
        rows = {r["mark"]: r for r in out["marks"]}
        assert rows[PROXIMA_TABLE]["launches"] == 5 and rows["fund:cu1450"]["launches"] == 2
        assert rows["fund:cu1450"]["reached_30"] == 1.0 and out["split"]["launches"] == 2 and out["funding_read"] == 5
        assert not any(m.startswith("fund:read") for m in rows)  # the "already read" note is not a tool
        calls = (rpc.tx_calls, rpc.sig_calls)
        again = asyncio.run(launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st,
                                         funding_of=PROXIMA_TABLE))  # fmt: skip
        assert (rpc.tx_calls, rpc.sig_calls) == calls and again["funding_read"] == 0
        assert {r["mark"]: r["launches"] for r in again["marks"]}["fund:cu1450"] == 2
    finally:
        st.close()


def test_a_launch_whose_create_block_buyers_were_relay_funded_is_marked_too():
    """The creator's funding found about 100 Proxima launches a day where the owner says 500: a dev launching from a
    wallet they already had is missed. The bundle wallets buying in the create's own block are funded by the same relay
    (8 of 8 around the owner's coins), so their first transaction is read as well."""
    from snipetech.analysis.launch_tools import launch_tools

    st = Store(":memory:")
    try:
        m = Market(st, addr("tm-coin"), addr("tm-dev"), NOW - 3000, 92_000_000)
        m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
        m.buy(addr("tm-team-a"), 2.5, 0)
        m.buy(addr("tm-team-b"), 2.5, 0)
        m.buy(addr("tm-late"), 1.0, 3)  # after the create block: not a bundle wallet, never looked up
        m.buy(addr("crowd"), 20.0, 5)
        m.commit()
        _coin(st, 90, run=False)  # a launch with nobody else in its create block
        rpc = _FundingRpc([addr("tm-team-b"), addr("tm-late")])
        out = asyncio.run(
            launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st, funding_of=PROXIMA_TABLE)
        )
        rows = {r["mark"]: r for r in out["marks"]}
        assert rows["team:cu1450"]["launches"] == 1 and "fund:cu1450" not in rows and out["team_read"] == 2
        assert rows["team:cu1450"]["examples"][0]["mint"] == addr("tm-coin") and not any("read" in k for k in rows)
        calls = (rpc.tx_calls, rpc.sig_calls)
        again = asyncio.run(
            launch_tools(rpc, st, NOW, hours=12, min_launches=1, marks_store=st, funding_of=PROXIMA_TABLE)
        )
        assert (rpc.tx_calls, rpc.sig_calls) == calls and again["team_read"] == 0
        assert {r["mark"] for r in again["marks"]} >= {"team:cu1450", PROXIMA_TABLE}
    finally:
        st.close()

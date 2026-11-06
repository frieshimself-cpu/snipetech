"""Market-wide discovery through a fake Birdeye client: coins from the tape, candidates merged, 30-day PnL attached, scores stored."""

import asyncio

from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.service import Service
from snipetech.store import Store


class FakeBirdeye:
    def __init__(self):
        self.calls = []
        self.used_cu = 0

    def gainers(self, period="1W", **kw):
        self.calls.append(("gainers", period))
        self.used_cu += 30
        return [{"wallet": "GAIN1", "trades": 40, "pnl_usd": 8000.0, "realized_usd": 7000.0},
                {"wallet": "PLATFORM", "trades": 900, "pnl_usd": 1e6, "realized_usd": 1e6}]

    def top_traders(self, mint, **kw):
        self.calls.append(("top_traders", mint))
        self.used_cu += 30
        return [{"wallet": "TOP1", "mint": mint, "trades": 6, "realized_usd": 400.0}]

    def wallet_pnl(self, wallet, duration="30d"):
        self.calls.append(("pnl", wallet))
        self.used_cu += 30
        return {"wallet": wallet, "duration": duration, "trades": 50, "win_rate": 0.55, "realized_usd": 1200.0, "total_usd": 1300.0,
                "invested_usd": 5000.0, "sold_usd": 6200.0, "unique_tokens": 20}

    def close(self):
        pass


def _store(tmp_path):
    st = Store(tmp_path / "t.db")
    now = 1_793_923_200
    for i, (mint, peak) in enumerate((("HOT", 90e9), ("WARM", 40e9), ("COLD", 5e9))):
        st._exec("insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after) values (?,0,1,0,?,?,'curve','x',1,1,1,0,0,?,1)",
                 (f"s{i}", now - 600, mint, int(peak)))
    st.set_label("PLATFORM", "some router", "service", "test")
    return st, now


def test_discover_birdeye_end_to_end(tmp_path):
    st, now = _store(tmp_path)
    svc = Service(Config(), store=st)
    fake = FakeBirdeye()
    rows = asyncio.run(svc.discover_birdeye(fake, days=1.0, top_coins=2, pnl_top=5, now=now))
    # the two hottest coins by peak curve, not the cold one
    assert ("top_traders", "HOT") in fake.calls and ("top_traders", "WARM") in fake.calls and ("top_traders", "COLD") not in fake.calls
    wallets = [r["wallet"] for r in rows]
    assert "PLATFORM" not in wallets and set(wallets) == {"GAIN1", "TOP1"}
    g = next(r for r in rows if r["wallet"] == "GAIN1")
    assert g["hist30"]["trades"] == 50 and g["hist30"]["realized_usd"] == 1200.0 and g["sources"] == ["gainers"]
    assert st.get_score("birdeye", "GAIN1")["hist30"]["win_rate"] == 0.55
    assert rows[0]["cu_used"] == fake.used_cu
    st.close()


def test_cli_accepts_source_birdeye():
    a = build_parser().parse_args(["discover-wallets", "--source", "birdeye", "--top-coins", "40", "--pnl-top", "80"])
    assert a.source == "birdeye" and a.top_coins == 40 and a.pnl_top == 80
    assert build_parser().parse_args(["discover-wallets"]).source == "tape"
    assert build_parser().parse_args(["copy-rank", "--source", "birdeye", "W1"]).source == "birdeye"


def test_birdeye_config_defaults():
    b = Config().birdeye
    assert b.daily_cu == 250_000 and b.top_coins == 30 and b.pnl_top == 100

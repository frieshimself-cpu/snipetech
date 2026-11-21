"""`oktech cycle --keep-plain SOL`: the run ends when its plain SOL cannot pay the next copy (21 Nov 2026).

OKTech keeps the wallet's wrapped SOL at 1.1 times a sniper's buy the whole time it runs, whatever its platforms (the
wrap test, 21 Nov 21:51-22:00: a curve-only sniper moved 0.12 SOL into wrapped SOL beside a 0.2 SOL buy at its start,
and when 0.12 was turned back into plain SOL while it ran, 3jjfyNcp... at 21:59:13, OKTech wrapped it again within two
seconds), and a buy on pump.fun's curve is paid in plain SOL only. So the plain SOL a copy can use is the wallet less
that wrapped SOL, and turning it back is undone at once. With nothing held and no buy on its way, a wallet whose plain
SOL is under one copy's (`keep_plain`: the buy with pump.fun's fee inside, the bid, the token account's rent) can only
send copies that fail: the run ends there and says so. A fall of the SOL that went into wrapped SOL is OKTech's top-up,
never a buy on its way."""

import asyncio

import pytest

from tests.test_oktech import SNIPER_IDS, W6, _cycle_svc, _SpentClient

T0 = 1_794_045_600.0


class Wallet:
    """The sniper wallet: plain and wrapped SOL; OKTech tops the wrapped SOL up to 1.1 x the buy at each start (and,
    as it does while a sniper runs, at every reading after one)."""

    def __init__(self, plain: float, wsol: float, buy: float):
        self.plain, self.wsol, self.buy, self.running = plain, wsol, buy, False

    def top_up(self) -> None:
        need = round(1.1 * self.buy - self.wsol, 9)
        if need > 0:
            d = min(need, self.plain)
            self.plain, self.wsol = round(self.plain - d, 9), round(self.wsol + d, 9)


def _run(monkeypatch, wallet: Wallet, *, coins=None, events=None, minutes=5, **kw):
    """The runner over two copy snipers on the fake clock; `events(now, wallet)` moves the wallet (a buy, a sale)."""
    clock, flag = {"t": T0}, {"stop": False}

    class Client(_SpentClient):
        async def start(self, i):
            wallet.running = True
            wallet.top_up()
            return await super().start(i)

    client = Client(clock, SNIPER_IDS, lambda i, now, last_start: False)

    def plain():
        if events:
            events(clock["t"], wallet)
        if wallet.running:
            wallet.top_up()
        return wallet.plain

    svc, store = _cycle_svc(monkeypatch, client, flag, clock=clock, balance=plain, coins=coins)
    for i in SNIPER_IDS:
        store.add_oktech_sniper(
            {"id": i, "kind": "copy", "source": "x", "payload": {"singleSnipe": True, "totalSnipes": 1}}
        )

    async def wrapped(address):
        return wallet.wsol

    monkeypatch.setattr(svc, "wsol_balance", wrapped)
    kw.setdefault("rearm_after", 1800)
    kw.setdefault("keep_plain", round(wallet.buy + 0.3, 6))
    try:
        rep = asyncio.run(svc.oktech_cycle(SNIPER_IDS, W6, None, minutes, max_loss=5.0, **kw))
    finally:
        store.close()
    return rep, client


def test_a_wallet_whose_plain_sol_cannot_pay_a_copy_ends_the_run_at_once(monkeypatch):
    """The owner's 10.37 SOL beside 5 SOL copies: OKTech wraps 5.5, 4.87 plain is left against the 5.3 a copy takes."""
    w = Wallet(plain=10.154, wsol=0.22, buy=5.0)
    rep, client = _run(monkeypatch, w, minutes=30)
    assert "cannot pay" in rep["reason"] and "no more re-arms" in rep["reason"], rep["reason"]
    assert "wrapped" in rep["reason"]
    assert max(t for _k, _i, t in client.timed) < T0 + 60  # at once, not after half an hour of failed sends
    assert rep["left_running"] == []  # nothing held: both stopped


def test_a_wallet_that_can_pay_runs_on(monkeypatch):
    """3.5 SOL copies from the same wallet: 3.85 wrapped, 6.5 plain against 3.8 a copy: the run goes on."""
    w = Wallet(plain=10.154, wsol=0.22, buy=3.5)
    rep, _client = _run(monkeypatch, w, minutes=3)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert any("OKTech wrapped" in e for e in rep["events"])


def test_the_top_up_is_no_buy_on_its_way(monkeypatch):
    """The fall of the SOL at a start went into wrapped SOL: no 30 s pause of the re-arms."""
    w = Wallet(plain=10.154, wsol=0.22, buy=3.5)
    _rep, client = _run(monkeypatch, w, minutes=3, rearm_after=60)
    assert [t for kind, _i, t in client.timed if kind == "start" and T0 + 55 <= t < T0 + 80]


def test_a_copy_that_leaves_too_little_plain_sol_ends_the_run_after_its_sale(monkeypatch):
    """A 3.5 SOL copy that lost 2.9: after its sale 3.6 plain SOL is left against 3.8, so the run ends then, never
    while the coin is held."""

    def coins(now):
        return [{"mint": "M1", "amount": 1.0, "value_sol": 0.6, "priced": True}] if T0 + 100 <= now < T0 + 112 else []

    def events(now, wallet):
        if T0 + 100 <= now and not getattr(wallet, "bought", False):
            wallet.plain, wallet.bought = round(wallet.plain - 3.71, 9), True
        if T0 + 112 <= now and not getattr(wallet, "sold", False):
            wallet.plain, wallet.sold = round(wallet.plain + 0.8, 9), True

    w = Wallet(plain=10.154, wsol=0.22, buy=3.5)
    rep, client = _run(monkeypatch, w, coins=coins, events=events, minutes=10)
    assert "cannot pay" in rep["reason"], rep["reason"]
    assert not [t for _k, _i, t in client.timed if T0 + 100 <= t < T0 + 112]  # nothing touched while it was held
    assert rep["left_running"] == []


def test_keep_plain_is_sane_and_reaches_the_runner(tmp_path, monkeypatch, capsys):
    from snipetech import cli
    from snipetech.service import Service

    with pytest.raises(SystemExit):
        _run(monkeypatch, Wallet(plain=10.0, wsol=0.0, buy=3.5), keep_plain=0.0)
    seen: list[dict] = []

    async def cycle(self, ids, address, floor_sol, max_minutes, max_fills=10, rearm_after=60.0, ignore_mints=None,
                    **kw):  # fmt: skip
        seen.append(kw)
        return {"events": ["done"]}

    monkeypatch.setattr(Service, "oktech_cycle", cycle)
    base = ["--db", str(tmp_path / "snipetech.db"), "oktech", "cycle", "--id", "a", "--address", W6, "--max-loss", "5"]
    assert cli.main([*base, "--keep-plain", "3.8"]) == 0 and seen[-1]["keep_plain"] == 3.8
    assert cli.main(base) == 0 and seen[-1]["keep_plain"] is None
    capsys.readouterr()
